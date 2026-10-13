"""MainAgent bridge to unmodified ToolSandbox tools, state and official evaluator."""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from src.agents.agent_loop import MainAgent
from src.agents.tool import Tool
from tool_sandbox.common.execution_context import RoleType, get_current_context
from tool_sandbox.common.message_conversion import Message, openai_tool_call_to_python_code
from tool_sandbox.common.tool_conversion import convert_to_openai_tools
from tool_sandbox.roles.base_role import BaseRole
from tool_sandbox.roles.execution_environment import ExecutionEnvironment
from tool_sandbox.roles.openai_api_user import OpenAIAPIUser

from .budget import CalibrationStopped


class VortoAgent(BaseRole):
    role_type = RoleType.AGENT

    def __init__(self, loop, client, *, message_limit=30):
        self.loop, self.client, self.agent = loop, client, None
        self.message_limit, self.origin = message_limit, None
        self.execution = ExecutionEnvironment()
        self.envelope_exhausted = False

    def respond(self, ending_index=None):
        messages = self.get_messages(ending_index)
        self.messages_validation(messages)
        messages = self.filter_messages(messages)
        if messages[-1].sender == RoleType.SYSTEM:
            return
        if self.origin is None:
            self.origin = get_current_context().max_sandbox_message_index
        available = self.get_available_tools()
        tools = []
        for schema in convert_to_openai_tools(available):
            function = schema['function']
            name = function['name']

            async def call(args, name=name):
                context = get_current_context()
                if context.max_sandbox_message_index - self.origin + 2 >= self.message_limit:
                    self.envelope_exhausted = True
                    raise CalibrationStopped('ToolSandbox message envelope exhausted')
                tool_id = 'call_' + uuid.uuid4().hex[:16]
                tool_call = SimpleNamespace(id=tool_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))
                code = openai_tool_call_to_python_code(tool_call, set(available), context.get_execution_facing_tool_name(name))
                self.add_messages([Message(sender=RoleType.AGENT, recipient=RoleType.EXECUTION_ENVIRONMENT,
                                           content=code, openai_tool_call_id=tool_id, openai_function_name=name)])
                self.execution.respond()
                last = self.get_messages()[-1]
                if last.sender != RoleType.EXECUTION_ENVIRONMENT or last.recipient != RoleType.AGENT:
                    raise RuntimeError('unexpected ToolSandbox tool response')
                return last.content

            tools.append(Tool(name, function.get('description', ''), {}, call,
                              input_schema=function['parameters'], serial=True,
                              read_only=name.startswith(('get_', 'search_', 'find_', 'convert_', 'calculate_'))))
        if self.agent is None:
            system = '\n'.join(message.content for message in messages if message.sender == RoleType.SYSTEM)
            self.agent = MainAgent(tools, llm=self.client, native=True, max_steps=12,
                                   extra_system=system, env_context=False, plan_tool=False, raise_llm_errors=True)
            self.agent.build_auto_continues = 0
        else:
            self.agent._tool_list = tools
            self.agent.tools = {tool.name: tool for tool in tools}
        reply = self.loop.run_until_complete(self.agent.run_turn(messages[-1].content, mode='build', emit=lambda text: None))
        if self.envelope_exhausted:
            raise CalibrationStopped('ToolSandbox message envelope exhausted')
        if not reply.strip():
            raise RuntimeError('empty assistant reply')
        self.add_messages([Message(sender=RoleType.AGENT, recipient=RoleType.USER, content=reply)])


class VortoUser(OpenAIAPIUser):
    """Keep the official simulator prompt/messages/termination tool, change provider only."""
    def __init__(self, loop, client):
        self.loop, self.client = loop, client

    def model_inference(self, openai_messages, openai_tools):
        kwargs = {'tools': list(openai_tools)} if isinstance(openai_tools, (list, tuple)) else {}
        response = self.loop.run_until_complete(self.client.chat(openai_messages, **kwargs))
        calls = [SimpleNamespace(id=call['id'], function=SimpleNamespace(name=call['name'], arguments=call['arguments']))
                 for call in response.get('tool_calls') or []]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=response.get('content'), tool_calls=calls or None))])
