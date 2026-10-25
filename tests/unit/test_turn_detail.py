import json

from src.gateway.session_store import save_session
from src.gateway.turn_digest import read_turn_detail


def test_detail_is_scoped_to_requested_root_session_and_turn_and_never_returns_history(tmp_path):
    save_session(str(tmp_path), 'sid-chat', [
        {'role':'user','rid':'r1','text':'检查状态'}, {'role':'assistant','rid':'r1','text':'已检查'},
        {'role':'user','rid':'r2','text':'别的请求'}, {'role':'assistant','rid':'r2','text':'不应出现'},
    ], [{'private_model_history':'never-return'}], [], activities=[
        {'type':'agent_tool','id':'t1','rid':'r1','name':'read_file','status':'completed',
         'result':'TOKEN=private-key\n普通结果', 'args':{'api_key':'private-key','path':'README.md'}},
        {'type':'agent_tool','id':'t2','rid':'r2','result':'other-turn-only'},
        {'type':'unknown','rid':'r1','secret':'unexpected'},
    ])
    value=read_turn_detail(str(tmp_path), 'chat','r1')
    assert value['request']=='检查状态' and value['reply']=='已检查' and value['has_reply']
    assert len(value['events'])==1 and value['events'][0]['id']=='t1'
    encoded=json.dumps(value,ensure_ascii=False)
    assert '普通结果' in encoded and '[REDACTED]' in encoded
    for hidden in ('private-key','never-return','other-turn-only','不应出现','unexpected'):
        assert hidden not in encoded
    assert read_turn_detail(str(tmp_path/'other-root'),'chat','r1') is None
    assert read_turn_detail(str(tmp_path),'chat','missing') is None
    assert read_turn_detail(str(tmp_path),'../chat','r1') is None


def test_missing_reply_and_bounded_event_tail_do_not_invent_completion(tmp_path):
    save_session(str(tmp_path),'sid-chat',[{'role':'user','rid':'r','text':'long '+('x'*2000)}],[],[],
                 activities=[{'type':'agent_tool','id':str(i),'rid':'r','status':'running','result':'z'*5000} for i in range(250)])
    value=read_turn_detail(str(tmp_path),'chat','r')
    assert not value['has_reply'] and not value['reply'] and value['truncated']
    assert len(value['events'])==200 and value['events'][0]['id']=='50'
    assert all(event['status']=='running' and len(event['result'])<=2000 for event in value['events'])
    assert len(value['request'])<12000


def test_hook_details_keep_the_recorded_event_tool_and_error_without_extra_fields(tmp_path):
    save_session(str(tmp_path), 'sid-chat', [{'role': 'user', 'rid': 'r', 'text': '检查'}], [], [],
                 activities=[{'type': 'agent_hook', 'id': 'hook', 'rid': 'r', 'status': 'failed',
                              'event': 'PreToolUse', 'tool': 'run_command', 'error': '检查失败',
                              'private_extra': 'not-for-display'}])
    event = read_turn_detail(str(tmp_path), 'chat', 'r')['events'][0]
    assert event['event'] == 'PreToolUse' and event['tool'] == 'run_command'
    assert event['error'] == '检查失败' and 'private_extra' not in event


def test_detail_preserves_recorded_time_duration_and_missing_status_without_inference(tmp_path):
    save_session(str(tmp_path), 'sid-chat', [{'role': 'user', 'rid': 'r', 'text': '检查'},
                                          {'role': 'assistant', 'rid': 'r', 'text': '已回复'}], [], [], activities=[
        {'type': 'agent_tool', 'id': 'unknown', 'rid': 'r', 'name': 'read_file'},
        {'type': 'agent_tool', 'id': 'timeout', 'rid': 'r', 'status': 'timed_out',
         'recorded_at': '2026-10-24T09:00:00+00:00', 'duration_ms': 1250},
    ])
    value = read_turn_detail(str(tmp_path), 'chat', 'r')
    assert value['has_reply'] and 'status' not in value['events'][0]
    assert value['events'][1] == {'type': 'agent_tool', 'id': 'timeout', 'rid': 'r', 'status': 'timed_out',
                                  'recorded_at': '2026-10-24T09:00:00+00:00', 'duration_ms': 1250}


def test_http_detail_reads_only_existing_archive_without_starting_a_session(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.web.routers.realtime import router
    monkeypatch.chdir(tmp_path)
    save_session(str(tmp_path),'sid-chat',[{'role':'user','rid':'r','text':'hello'}],[],[])
    app=FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        response=client.get('/api/agent/activity/chat/r')
        assert response.status_code==200 and response.json()['request']=='hello'
        assert response.json()['source']=='session_archive'
        assert client.get('/api/agent/activity/chat/other').status_code==404
        assert client.get('/api/agent/activity/other/r').status_code==404
