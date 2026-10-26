import hashlib

from contracts import Identifier
from pydantic import TypeAdapter

_IDENTIFIER = TypeAdapter(Identifier)


class StoragePaths:
    @staticmethod
    def run(organisation_id: str, run_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/runs/{_segment(run_id)}"

    @staticmethod
    def step(organisation_id: str, run_id: str, command_id: str) -> str:
        return f"{StoragePaths.run(organisation_id, run_id)}/steps/{_segment(command_id)}"

    @staticmethod
    def outbox(organisation_id: str, event_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/outbox/{_segment(event_id)}"

    @staticmethod
    def delivery(organisation_id: str, run_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/deliveries/{_segment(run_id)}"

    @staticmethod
    def connection(organisation_id: str, connection_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/connections/{_segment(connection_id)}"

    @staticmethod
    def application(organisation_id: str, application_id: str) -> str:
        organisation = StoragePaths.organisation(organisation_id)
        return f"{organisation}/applications/{_segment(application_id)}"

    @staticmethod
    def environment(organisation_id: str, environment_id: str) -> str:
        organisation = StoragePaths.organisation(organisation_id)
        return f"{organisation}/environments/{_segment(environment_id)}"

    @staticmethod
    def service(organisation_id: str, service_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/services/{_segment(service_id)}"

    @staticmethod
    def credential(organisation_id: str, credential_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/credentials/{_segment(credential_id)}"

    @staticmethod
    def binding(organisation_id: str, binding_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/bindings/{_segment(binding_id)}"

    @staticmethod
    def generation(organisation_id: str, generation_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/generations/{_segment(generation_id)}"

    @staticmethod
    def playbook(organisation_id: str, playbook_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/playbooks/{_segment(playbook_id)}"

    @staticmethod
    def playbook_version(organisation_id: str, playbook_id: str, version_id: str) -> str:
        root = StoragePaths.playbook(organisation_id, playbook_id)
        return f"{root}/versions/{_segment(version_id)}"

    @staticmethod
    def walkthrough(organisation_id: str, playbook_id: str, source_id: str) -> str:
        root = StoragePaths.playbook(organisation_id, playbook_id)
        return f"{root}/walkthroughs/{_segment(source_id)}"

    @staticmethod
    def incident(organisation_id: str, incident_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/incidents/{_segment(incident_id)}"

    @staticmethod
    def ingestion(organisation_id: str, source: str, event_id: str, kind: str) -> str:
        identity = hashlib.sha256(f"{source}\0{event_id}\0{kind}".encode()).hexdigest()
        return f"{StoragePaths.organisation(organisation_id)}/ingestion/event_{identity}"

    @staticmethod
    def approval(organisation_id: str, approval_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/approvals/{_segment(approval_id)}"

    @staticmethod
    def notification_collection(organisation_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/notifications"

    @staticmethod
    def notification(organisation_id: str, notification_id: str) -> str:
        root = StoragePaths.notification_collection(organisation_id)
        return f"{root}/{_segment(notification_id)}"

    @staticmethod
    def notification_endpoint_collection(organisation_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/notification-endpoints"

    @staticmethod
    def notification_endpoint(organisation_id: str, endpoint_id: str) -> str:
        root = StoragePaths.notification_endpoint_collection(organisation_id)
        return f"{root}/{_segment(endpoint_id)}"

    @staticmethod
    def notification_delivery(organisation_id: str, notification_id: str, delivery_id: str) -> str:
        root = StoragePaths.notification(organisation_id, notification_id)
        return f"{root}/notification-deliveries/{_segment(delivery_id)}"

    @staticmethod
    def action(organisation_id: str, action_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/actions/{_segment(action_id)}"

    @staticmethod
    def audit(organisation_id: str, event_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/audit/{_segment(event_id)}"

    @staticmethod
    def audit_outbox(organisation_id: str, event_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/audit-outbox/{_segment(event_id)}"

    @staticmethod
    def audit_delivery(organisation_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/audit-state/delivery"

    @staticmethod
    def audit_head(organisation_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/audit-state/head"

    @staticmethod
    def evidence(organisation_id: str, evidence_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/evidence/{_segment(evidence_id)}"

    @staticmethod
    def browser(organisation_id: str, session_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/browsers/{_segment(session_id)}"

    @staticmethod
    def browser_egress(region: str) -> str:
        return f"browser-egress/{_segment(region)}"

    @staticmethod
    def setup(organisation_id: str, setup_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/setups/{_segment(setup_id)}"

    @staticmethod
    def connection_waiter(organisation_id: str, connection_id: str) -> str:
        root = StoragePaths.organisation(organisation_id)
        return f"{root}/connection-waiters/{_segment(connection_id)}"

    @staticmethod
    def github_onboarding(organisation_id: str, onboarding_id: str) -> str:
        root = StoragePaths.organisation(organisation_id)
        return f"{root}/github-onboarding/{_segment(onboarding_id)}"

    @staticmethod
    def github_installation(organisation_id: str, installation_id: int) -> str:
        root = StoragePaths.organisation(organisation_id)
        return f"{root}/github-installations/{installation_id}"

    @staticmethod
    def github_repository(organisation_id: str, repository_id: int) -> str:
        root = StoragePaths.organisation(organisation_id)
        return f"{root}/github-repositories/{repository_id}"

    @staticmethod
    def github_installation_index(installation_id: int) -> str:
        return f"github-installation-index/{installation_id}"

    @staticmethod
    def github_webhook_receipt(installation_id: int) -> str:
        return f"github-webhook-receipts/{installation_id}"

    @staticmethod
    def capture(organisation_id: str, session_id: str, capture_id: str) -> str:
        root = StoragePaths.browser(organisation_id, session_id)
        return f"{root}/captures/{_segment(capture_id)}"

    @staticmethod
    def browser_action(organisation_id: str, session_id: str, action_id: str) -> str:
        root = StoragePaths.browser(organisation_id, session_id)
        return f"{root}/actions/{_segment(action_id)}"

    @staticmethod
    def computer_use_activity(organisation_id: str, session_id: str, activity_id: str) -> str:
        root = StoragePaths.browser(organisation_id, session_id)
        return f"{root}/activity/{_segment(activity_id)}"

    @staticmethod
    def probe(organisation_id: str, probe_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/probes/{_segment(probe_id)}"

    @staticmethod
    def probe_version(organisation_id: str, version_id: str) -> str:
        organisation = StoragePaths.organisation(organisation_id)
        return f"{organisation}/probe-versions/{_segment(version_id)}"

    @staticmethod
    def report(organisation_id: str, report_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/reports/{_segment(report_id)}"

    @staticmethod
    def plan(organisation_id: str, plan_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/plans/{_segment(plan_id)}"

    @staticmethod
    def control_version(organisation_id: str, credential_id: str, version_id: str) -> str:
        credential = StoragePaths.credential(organisation_id, credential_id)
        return f"{credential}/control-versions/{_segment(version_id)}"

    @staticmethod
    def recovery(organisation_id: str, recovery_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/recovery/{_segment(recovery_id)}"

    @staticmethod
    def recovery_result(organisation_id: str, recovery_id: str, result_id: str) -> str:
        root = StoragePaths.recovery(organisation_id, recovery_id)
        return f"{root}/results/{_segment(result_id)}"

    @staticmethod
    def stage(organisation_id: str, execution_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/stages/{_segment(execution_id)}"

    @staticmethod
    def tool(organisation_id: str, request_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/tools/{_segment(request_id)}"

    @staticmethod
    def agent(organisation_id: str, agent_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/agents/{_segment(agent_id)}"

    @staticmethod
    def agent_session(organisation_id: str, session_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/agent-sessions/{_segment(session_id)}"

    @staticmethod
    def agent_memory(organisation_id: str, memory_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/agent-memory/{_segment(memory_id)}"

    @staticmethod
    def lock(organisation_id: str, credential_id: str) -> str:
        organisation = StoragePaths.organisation(organisation_id)
        return f"{organisation}/credentials/{_segment(credential_id)}/locks/rotation"

    @staticmethod
    def dedupe(organisation_id: str, source: str, event_id: str) -> str:
        identity = hashlib.sha256(f"{source}\0{event_id}".encode()).hexdigest()
        return f"{StoragePaths.organisation(organisation_id)}/dedupe/event_{identity}"

    @staticmethod
    def principal(organisation_id: str, principal_id: str) -> str:
        return f"{StoragePaths.principal_collection(organisation_id)}/{principal_id}"

    @staticmethod
    def principal_collection(organisation_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/principals"

    @staticmethod
    def invitation(organisation_id: str, invitation_id: str) -> str:
        return f"{StoragePaths.invitation_collection(organisation_id)}/{_segment(invitation_id)}"

    @staticmethod
    def invitation_collection(organisation_id: str) -> str:
        return f"{StoragePaths.organisation(organisation_id)}/team-invitations"

    @staticmethod
    def organisation(organisation_id: str) -> str:
        return f"organisations/{_segment(organisation_id)}"


def _segment(value: str) -> str:
    return _IDENTIFIER.validate_python(value)
