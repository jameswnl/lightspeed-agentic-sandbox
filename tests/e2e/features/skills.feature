Feature: Skills
  Verifies: .ai/spec/provider-contract.md (Skill discovery, Tool execution)
  The system discovers skills from the mounted directory, runs their
  scripts, and serves non-skill queries normally when skills are present.

  Scenario: System starts and serves requests with skills mounted
    Given the sandbox service is running with skills
    And a simple non-skill query has been prepared
    When I POST run with the prepared query and no output schema
    Then the HTTP response status code is 200
    And the response has a non-empty summary
    And success is true

  Scenario: System runs a skill and returns structured output
    Given the sandbox service is running with skills
    And the echo-token skill query has been prepared
    When I POST run with the prepared echo-token query
    Then the HTTP response status code is 200
    And the skill script wrote a token file to disk
    And the response JSON validates against the output schema
    And the response contains the generated token

  Scenario: Non-skill query is unaffected by mounted skills
    Given the sandbox service is running with skills
    And no output schema will be sent
    When I POST run with the prepared query and no output schema
    Then the HTTP response status code is 200
    And the response has a non-empty summary
    And success is true
