Feature: Sandbox E2E contract
  Verifies: .ai/spec/what/health-probes.md (probes), .ai/spec/what/run-api.md (timeout, context)
  Harness: .ai/spec/what/e2e-testing.md
  Live tests against one sandbox (see scripts/e2e-containers.sh). Exact context prefix
  formatting: test_routes.py. Run error rules 22–23 and no-500: test_routes.py,
  structured_output.feature.

  Scenario: Liveness probe returns ok
    Given the sandbox service is running
    When I GET /health
    Then the HTTP response status code is 200
    And the response body status is ok

  Scenario: Readiness probe returns ok when credentials are configured
    Given provider credentials are configured
    And the sandbox service is running
    When I GET /ready
    Then the HTTP response status code is 200
    And the response body status is ok

  Scenario: Run times out when timeout_ms is too short
    Given the sandbox service is running
    And a query that will exceed the timeout has been prepared
    When I POST run with timeout_ms 1
    Then the HTTP response status code is 200
    And success is false

  Scenario: Target namespaces from context reach the model
    Given the sandbox service is running
    And a context with target namespaces and an echo output schema have been prepared
    When I POST run with the prepared context and schema
    Then the HTTP response status code is 200
    And success is true
    And the response namespaces field matches the prepared context

  Scenario: Previous attempts from context reach the model
    Given the sandbox service is running
    And a context with previous attempts and an echo output schema have been prepared
    When I POST run with the prepared context and schema
    Then the HTTP response status code is 200
    And success is true
    And the response first failure reason matches the prepared context

  Scenario: Approved option from context reaches the model
    Given the sandbox service is running
    And a context with approved option and an echo output schema have been prepared
    When I POST run with the prepared context and schema
    Then the HTTP response status code is 200
    And success is true
    And the response approved option fields match the prepared context
