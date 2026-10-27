# Kinu infrastructure

AWS is the deployment target. `auth.yaml` provisions a separate Kinu Cognito user pool,
public code-flow client, and hosted sign-in domain. Application hosting is still pending.

```sh
aws cloudformation deploy --region eu-west-2 --stack-name kinu-auth \
  --template-file infra/auth.yaml \
  --parameter-overrides Stage=production PublicUrl=https://kinu.example.com
aws cloudformation describe-stacks --region eu-west-2 --stack-name kinu-auth \
  --query 'Stacks[0].Outputs'
```

Replace the example origin with the deployed app origin. For predeployment sign-in tests,
omit the parameter overrides to use the demo pool and localhost callback. Set the returned
`PoolId`, `ClientId`, and `DomainUrl` as the API's Cognito environment values.
The Lite tier uses the classic hosted UI. User pool deletion is protected; stack deletion
retains the pool. The stack creates no IAM roles, access keys, or compute instances.

The API requires Cognito configuration and a PostgreSQL database. Set an HTTPS public URL,
use TLS for database access, and keep database credentials in the API environment. The
Agent-Native service verifies sessions through the API and must not receive database credentials.

AgentCore Browser, Secrets Manager, mailbox ingestion, and runtime update adapters are
not connected yet. Local tests verify the application before deployment; they do not
provision AWS resources or prove a live rotation.
