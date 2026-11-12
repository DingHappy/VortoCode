"""Trusted host adapter for workflow-only fault tests; HTTP auth is tested separately."""
from src.workflow.staff.owner_confirmation import owner_route


def owner_decide(approvals, proposal_id, approve, expected_digest, **changes):
    proposal = approvals.get(proposal_id)
    binding = {key: proposal[key] for key in ('nonce', 'round', 'instance')}
    with owner_route(approvals.registry.root):
        return approvals.decide(proposal_id, approve, expected_digest, **{**binding, **changes})


def owner_action(service, task, card, approve=True, **changes):
    payload = card['payload']
    binding = {key: payload[key] for key in ('nonce', 'round', 'instance', 'runtime')}
    with owner_route(service.root):
        return service.actions.decide(task.id, payload['id'], approve=approve,
            expected_digest=card['digest'], **{**binding, **changes})


def proposal_body(proposal, *, approve=True, **changes):
    return {'approve': approve, **{key: proposal[key] for key in ('digest', 'nonce', 'round', 'instance')}, **changes}


def action_body(card, *, approve=True, reason='', **changes):
    return {'approve': approve, 'reason': reason, 'digest': card['digest'],
            **{key: card['payload'][key] for key in ('nonce', 'round', 'instance', 'runtime')}, **changes}
