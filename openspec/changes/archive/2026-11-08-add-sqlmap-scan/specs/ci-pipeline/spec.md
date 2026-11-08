## RENAMED Requirements

- FROM: `### Requirement: Dynamic scan in CI`
- TO: `### Requirement: SQL injection scan in CI`

## MODIFIED Requirements

### Requirement: Pull-request workflow
The project SHALL run a GitHub Actions workflow on every pull request targeting `main` and on manual dispatch, and SHALL NOT build or publish images or deploy from it.

#### Scenario: Pull request opened
- **WHEN** a pull request to `main` is opened or updated
- **THEN** the test, static security, dependency audit and SQL injection scan jobs run

#### Scenario: Push to a feature branch only
- **WHEN** commits are pushed to a branch with no pull request
- **THEN** the workflow does not run

### Requirement: SQL injection scan in CI
The workflow SHALL start a throwaway instance from the repository's image with scratch credentials, run sqlmap against the string-valued parameters of every operation in the application's OpenAPI document (public ones unauthenticated, the others with a throwaway superadmin or organization admin token, whichever the operation accepts), publish the sqlmap output as a workflow artifact, and fail the job when a parameter is reported injectable or the scan cannot run.

#### Scenario: Injectable parameter
- **WHEN** sqlmap confirms that a parameter of an endpoint is injectable
- **THEN** the job fails and names the endpoint and parameter

#### Scenario: Reports published
- **WHEN** the scan job finishes, whether it passed or failed
- **THEN** the sqlmap output is attached to the run and contains no token, password or secret key

#### Scenario: Clean scan
- **WHEN** no parameter is reported injectable
- **THEN** the job passes and the artifact lists every parameter as scanned (with the status of its baseline request), not reached (with that status) or skipped (with the reason), and the job log states how many scannable parameters were reached and how many were not

#### Scenario: Parameter that cannot be reached
- **WHEN** the unmodified request of an operation is answered with a 4xx other than the expected 401 of a public sign-in (validation, wrong scope, missing object)
- **THEN** its parameters are listed as not reached with that status, sqlmap is not counted as having tested them, and the job does not fail

#### Scenario: Baseline failure
- **WHEN** the unmodified request of an operation is answered with a server error or not answered at all
- **THEN** the job fails and names the operation

#### Scenario: New endpoint is covered
- **WHEN** a pull request adds an operation to the API
- **THEN** its string parameters are scanned without any change to the workflow

#### Scenario: Scan setup fails
- **WHEN** the instance does not become healthy, sign-in or seeding fails or sqlmap cannot be installed or run
- **THEN** the job fails, so a missing scan is never reported as a clean one
