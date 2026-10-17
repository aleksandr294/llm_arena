# Spec Delta

## Purpose

Defines the metrics the platform reports for an experiment and how models are compared, computed only from stored results, with measured and estimated values kept distinct and unknown values reported as unknown.

## ADDED Requirements

### Requirement: Experiment summary
The system SHALL return, for an experiment, the number of results by status (pending, running, succeeded, failed, cancelled), the total, the experiment status and whether every result has finished, computed from the stored results. An unknown experiment SHALL return 404.

#### Scenario: Mixed statuses
- **WHEN** an experiment has 3 succeeded, 1 failed and 2 pending results
- **THEN** the summary shows those counts, a total of 6 and finished false

#### Scenario: Unknown experiment
- **WHEN** a client requests the summary of an experiment that does not exist
- **THEN** the response is 404

### Requirement: Model comparison
The system SHALL return one comparison entry per target (provider and model) of the experiment, each computed from only that target's results, so that targets can be compared on the same cases. Targets with no finished results SHALL still appear with zero counts and null statistics.

#### Scenario: Two targets
- **WHEN** an experiment ran 3 cases on two models
- **THEN** the comparison has two entries, each based on that model's 3 results

### Requirement: Error rate
The system SHALL report per target the counts of succeeded, failed, cancelled and unfinished results, the error rate as failed divided by succeeded plus failed (null when none finished), and the number of failures per error kind.

#### Scenario: Error rate
- **WHEN** a target has 3 succeeded and 1 failed result and 1 cancelled result
- **THEN** its error rate is 0.25 and the cancelled result is not counted in it

#### Scenario: No finished results
- **WHEN** a target has only pending results
- **THEN** its error rate is null

### Requirement: Latency statistics
The system SHALL report per target, over succeeded results with a measured latency, the count of results used, and the minimum, mean, median, 95th percentile and maximum latency in milliseconds, with null statistics when no result has a measured latency.

#### Scenario: Latency stats
- **WHEN** a target has latencies of 100, 200 and 300 milliseconds
- **THEN** the minimum is 100, the mean 200, the median 200 and the maximum 300

#### Scenario: Latency unknown
- **WHEN** no succeeded result of a target has a measured latency
- **THEN** all latency statistics are null and the count used is 0

### Requirement: Token totals with coverage
The system SHALL report per target the sums of input, output and total tokens over the results where the provider reported them, together with the number of succeeded results that reported each, and SHALL NOT treat missing values as zero or derive missing totals.

#### Scenario: Partial usage
- **WHEN** 2 of 3 succeeded results reported input tokens 10 and 20
- **THEN** the input token sum is 30 with a coverage of 2 of 3

### Requirement: Quality from evaluators
The system SHALL report per target and per configured evaluator the counts of passed, failed, skipped and error outcomes and the pass rate as passed divided by passed plus failed (null when neither exists), and SHALL NOT combine evaluators into a single score.

#### Scenario: Pass rate
- **WHEN** an evaluator has 3 passed, 1 failed and 2 skipped outcomes for a target
- **THEN** its pass rate is 0.75 and the skipped count is 2

### Requirement: Estimated cost with pricing version
The system SHALL compute an estimated cost per target only from reported input and output tokens and operator-supplied rates for the requested model, SHALL label it as an estimate, and SHALL report the pricing version, currency and rates used, the number of results included, and null when the target has no rates or no results with both token counts.

#### Scenario: Cost computed
- **WHEN** rates are 1.00 per million input tokens and 2.00 per million output tokens and a target reported 1000000 input and 500000 output tokens
- **THEN** the estimated cost is 2.00 and the response names the pricing version

#### Scenario: Model not priced
- **WHEN** the requested model has no configured rates
- **THEN** the estimated cost is null and the target is listed as unpriced

#### Scenario: No pricing configured
- **WHEN** no pricing file is configured
- **THEN** every estimated cost is null and the pricing version is null

### Requirement: Pricing configuration validation
The system SHALL load pricing from an operator-supplied file with a version string, a currency and non-negative rates per provider and model, SHALL ship no rates as facts, and SHALL fail with a clear error naming the file when the configured file is missing or invalid, without affecting other metrics.

#### Scenario: Invalid pricing file
- **WHEN** the configured pricing file has a negative rate
- **THEN** cost-dependent responses report an error that names the file and the other metrics remain available

### Requirement: Per-case results
The system SHALL list the experiment's test cases in position order with pagination (limit 1 to 100, default 50, offset 0 or more) and a total, each with, per target, the result status, attempts, latency, token counts, error kind and evaluator outcomes, and SHALL include response text only when explicitly requested.

#### Scenario: Cases listed
- **WHEN** an experiment has 3 cases and 2 targets and a client lists cases
- **THEN** each case entry has two target results and no response text

#### Scenario: Responses requested
- **WHEN** a client requests responses
- **THEN** each succeeded target result includes its stored response text exactly as stored

### Requirement: Metrics match stored data
The system SHALL compute all metrics from the stored results and evaluation outcomes at request time, so reported values equal what the stored rows imply, and SHALL NOT log prompts, responses or expected outputs.

#### Scenario: Consistency
- **WHEN** a result is stored and the comparison is requested
- **THEN** the counts, latency and tokens reflect exactly the stored rows
