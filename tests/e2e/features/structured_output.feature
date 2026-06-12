Feature: Structured output via POST /v1/agent/run
  Verifies: .ai/spec/what/run-api.md (rules 18–20, structured response shaping)
  Verifies: .ai/spec/what/provider-contract.md (Structured output)
  Live contract tests for one sandbox container per process (see scripts/e2e-containers.sh).

  Scenario: Run with flat schema and required fields
    Given the sandbox service is running
    And a flat output schema with required fields has been prepared
    When I POST run with the prepared schema and query
    Then the HTTP response status code is 200
    And the response includes success summary and ticketId fields
    And the response JSON validates against the output schema

  Scenario: Run with nested schema
    Given the sandbox service is running
    And a nested output schema has been prepared
    When I POST run with the prepared schema and query
    Then the HTTP response status code is 200
    And the response JSON validates against the output schema

  Scenario: Run without output schema
    Given the sandbox service is running
    And no output schema will be sent
    When I POST run with the prepared query and no output schema
    Then the HTTP response status code is 200
    And the response has a non-empty summary
    And success is true

  Scenario: Adversarial schema does not return HTTP 500
    Given the sandbox service is running
    And an adversarial output schema and prompt have been prepared
    When I POST run with the prepared schema and query
    Then the HTTP response status code is 200 and the envelope has success and summary
