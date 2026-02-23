# Admin Backend Concept

## Overview
An additional, protected backend for the MCP server to provide administrative functionalities. This backend will be isolated from the main server and accessible only to authorized personnel.

## Key Features
1. **Protected Access**: Secure authentication and authorization mechanisms.
2. **Log File Management**: View, download, and analyze server logs, both default access log and the token log, keep track of outgoing LLM calls
3. **Token Management**: Create, revoke, and manage access tokens.
4. **Database Browser**: Easy-to-use interface for querying and managing the database.
5. **Source Management**: Allow to maintain the `config.yaml` sources via the admin interface (add/remove/update sources, trigger re-scraping, etc.) 
6. **Extended Source Management** Interface to also upload pdf files to `/data` for Medium and LinkedIn profiles and subsequently edit the created *yaml* files
7. **Trigger Scraping and Enrichment**: Job Manager to mnually trigger scraping and enrichment processes for specific sources or entities. Watch progress in a dashboard and view results once completed.


## Technical Details
- **Port**: Run on a separate port (e.g., `8081`) to isolate from the main server.
- **Authentication**: Use OAuth2 or JWT for secure access.
- **Authorization**: Single highly protected distinct Admin account to restrict functionalities

## Endpoints
- based on key features, the following endpoints are planned:
  - `GET /admin/logs` — View and download log files
  - `POST /admin/tokens` — Create new access tokens
  - `DELETE /admin/tokens/{token_id}` — Revoke access tokens
  - `GET /admin/db` — Browse and query the database
  - `POST /admin/sources` — Add new sources to `config.yaml`
  - `PUT /admin/sources/{source_id}` — Update existing sources in `config.yaml`
  - `DELETE /admin/sources/{source_id}` — Remove sources from `config.yaml`
  - `POST /admin/scrape` — Trigger scraping and enrichment for specific sources or entities

## Security Measures
- **Rate Limiting**: Prevent brute force attacks.
- **IP Whitelisting**: Restrict access to specific IP addresses.
- **HTTPS**: Enforce secure communication.

## Implementation Steps
1. Set up a new FastAPI instance on a separate port.
2. Everything runs in a different folder, e.g. `/admin` to keep it isolated from the main server.
3. Implement authentication and authorization.
4. Develop endpoints for log management, token management, and database browsing.
5. No integration with the main server is needed, but ensure the admin backend can access the same database and resources.
6. Simple, plain, intuitive UI
7. Test and deploy.
8. Try to avoid extra frameworks and dependencies to keep it lightweight and maintainable. Focus on core functionalities first, then iterate based on needs.

## Dependencies
- FastAPI for the backend framework.
- OAuth2/JWT libraries for authentication.
- Database drivers for querying the database.

## Files to Adjust
- `admin/main.py`: Add admin backend initialization and routing.
- `admin/dependencies/access_control.py`: Extend access control for admin-specific roles.
- `admin/routers/__init__.py`: Add admin router imports.
- `admin/routers/admin.py`: Create a new router for admin-specific endpoints.

## Files to Consider
- `db/models.py`: Ensure database models support admin operations.
- `admin/mcp_tools.py`: Extend tools for admin-specific functionalities if needed.
- `admin/session_tracker.py`: Track admin sessions separately if required.

## Detailed Plan
1. **Setup Admin Backend**:
   - Create a new FastAPI instance in `admin/main.py` for the admin backend.
   - Configure it to run on a separate port (e.g., `8081`).
   - Ensure CORS and other middleware are appropriately configured for admin use.

2. **Authentication and Authorization**:
   - Extend `admin/dependencies/access_control.py` to include admin-specific roles.
   - Implement JWT or OAuth2 for secure access.
   - Ensure all admin endpoints require elevated access tokens.

3. **Develop Admin Endpoints**:
   - Create `admin/routers/admin.py` to house all admin-specific endpoints.
   - Implement endpoints for:
     - Log file management (`/admin/logs`).
     - Token management (`/admin/tokens`).
     - Database browsing (`/admin/db`).
   - Ensure all endpoints are protected and follow the same structure as existing routers.

4. **Integrate with MCP Server**:
   - Ensure the admin backend can access the same database and resources as the main server.
   - Reuse existing utilities and dependencies where possible.

5. **Testing and Deployment**:
   - Write comprehensive tests for all admin endpoints.
   - Ensure security measures are thoroughly tested.
   - Deploy the admin backend alongside the main server.

## Dependencies
- FastAPI for the backend framework.
- OAuth2/JWT libraries for authentication.
- Database drivers for querying the database.
- `python-jose` for JWT token handling.
- `passlib` for password hashing.

## Security Considerations
- **Rate Limiting**: Implement rate limiting to prevent brute force attacks.
- **IP Whitelisting**: Restrict access to specific IP addresses.
- **HTTPS**: Enforce secure communication.
- **Input Validation**: Validate all inputs to prevent injection attacks.
- **Logging**: Log all administrative actions for auditing.

## Testing and Deployment Strategy
1. **Unit Testing**: Write unit tests for all admin endpoints using `pytest`.
2. **Integration Testing**: Test the integration with the main MCP server.
3. **Security Testing**: Perform penetration testing to identify vulnerabilities.
4. **Deployment**: Deploy the admin backend alongside the main server using Docker or a similar containerization tool.

## Notes
- Ensure the admin backend is thoroughly tested for security vulnerabilities.
- Document all endpoints and functionalities for future reference.
