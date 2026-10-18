## Interactive API documentation

[Back to the README](../README.md)

With the API running:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI schema: `http://localhost:8000/openapi.json`

The admin panel is not part of the schema. To call protected endpoints from
Swagger UI: run `POST /api/v1/auth/login` there (or with curl), copy the
`access_token` from the response, click **Authorize**, paste the token alone
(without `Bearer `) and confirm. The token is remembered across page reloads
(`persistAuthorization`) until you log out of the dialog. Access tokens
expire (15 minutes by default): log in again or use `POST /auth/refresh`, and
re-authorize.

Set `API_DOCS_ENABLED=false` to turn all three off (they answer 404); the API
itself is unaffected. Swagger UI and ReDoc load their JavaScript and CSS from
a CDN by default (FastAPI's stock behaviour), so opening them contacts a
third party; set `API_DOCS_ENABLED=false` for production exposure.
