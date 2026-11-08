# Spec Delta

## ADDED Requirements

### Requirement: Failed writes keep earlier changes
The system SHALL turn a unique-constraint race on a user or organization write into the normal conflict answer without discarding other changes already made in the same request.

#### Scenario: Unrelated pending change survives a conflict
- **WHEN** a service call loses a unique-key race while the session holds an unrelated pending change made earlier in the same operation
- **THEN** the call raises the conflict error and the unrelated change is still pending in the session

### Requirement: Concurrent user updates do not lose changes
The system SHALL serialize concurrent updates of one user so that each change (role, activation, password) is applied on top of the previous one.

#### Scenario: Parallel role and password update
- **WHEN** two admins update the role and the password of the same user at the same time
- **THEN** the final user has both the new role and the new password

### Requirement: Peer admin and email-conflict behavior is documented
The system SHALL document that an admin can change other admins of the same organization and that a 409 on user creation reveals an email exists in any organization.

#### Scenario: Documentation
- **WHEN** an integrator reads docs/authentication.md
- **THEN** both behaviors and their accepted risk are stated
