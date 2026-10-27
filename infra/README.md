# Kinu infrastructure

AWS is the deployment target. Deployable infrastructure definitions are still pending.

The API requires Cognito configuration and a PostgreSQL database. Set an HTTPS public URL,
use TLS for database access, and keep database credentials in the API environment. The
Agent-Native service verifies sessions through the API and must not receive database credentials.

AgentCore Browser, Secrets Manager, mailbox ingestion, and runtime update adapters are
not connected yet. Local tests verify the application before deployment; they do not
provision AWS resources or prove a live rotation.
