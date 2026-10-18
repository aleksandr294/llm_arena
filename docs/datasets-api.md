## Datasets API

[Back to the README](../README.md)

Every dataset, experiment, evaluation, metrics and export endpoint requires
an access token (see Authentication and users), and each caller sees only
the data allowed by their role (see Access control and ownership). The
examples below assume `TOKEN` holds an access token from `/auth/login` of a
`staff` or `admin` user.

A dataset is a named, ordered set of test cases. Test cases are append-only:
each gets the next position (1, 2, ...) in its dataset. Content is stored and
returned exactly as supplied.

```bash
# create a dataset (201); name is trimmed, description is optional
curl -H "Authorization: Bearer $TOKEN" -X POST localhost:8000/api/v1/datasets \
    -H 'Content-Type: application/json' \
    -d '{"name": "Capitals", "description": "Geography smoke test"}'

# list (limit 1-100, default 50; offset >= 0) and get (includes case_count)
curl -H "Authorization: Bearer $TOKEN" 'localhost:8000/api/v1/datasets?limit=10&offset=0'
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/datasets/<dataset_id>

# add a test case (201); expected_output and metadata (object) are optional
curl -H "Authorization: Bearer $TOKEN" -X POST localhost:8000/api/v1/datasets/<dataset_id>/cases \
    -H 'Content-Type: application/json' \
    -d '{"input": "Capital of France?", "expected_output": "Paris",
         "metadata": {"topic": "geo"}}'

# list test cases ordered by position
curl -H "Authorization: Bearer $TOKEN" 'localhost:8000/api/v1/datasets/<dataset_id>/cases?limit=10'
```

List responses have the form `{items, total, limit, offset}`. Invalid input
returns 422 naming the field; an unknown dataset returns 404.
