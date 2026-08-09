# Test context

Ordinary tests must not require live X access or real credentials. Capability
contract/planner tests are protocol-neutral and should prove that adding a
capability does not require queue/TaskRepository changes.

PostgreSQL/Redis integration tests verify durable control-plane behavior.
Protocol request construction, X parsers and X pagination tests belong in
`techtiesai-png/X-rev-os`, not here.

