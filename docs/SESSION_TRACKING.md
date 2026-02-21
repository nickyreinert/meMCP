# Session Tracking & Coverage Monitoring

## Overview

The API now tracks client sessions and monitors endpoint coverage to help LLM agents understand their exploration progress.

## Features

### 1. Session Tracking
- **Unique Visitor ID**: Hash of `anonymized_ip + user_agent`
- **IP Anonymization**: Last octet removed (e.g., `192.168.1.x`)
- **Session Duration**: 5 hours (configurable in `config.yaml`)
- **Automatic Logging**: All requests logged to `logs/api_access.log`

### 2. Coverage Monitoring
- **Relevant Endpoints** (10 total):
  - `/greeting` (personal identity)
  - `/stages` (career timeline list) **- paginated**
  - `/stages/{id}` (single stage detail)
  - `/oeuvre` (projects/articles list) **- paginated**
  - `/oeuvre/{id}` (single project detail)
  - `/skills` (all skills) **- paginated**
  - `/skills/{name}` (skill detail)
  - `/technology` (all technologies) **- paginated**
  - `/technology/{name}` (technology detail)
  - `/tags/{tag_name}` (tag detail with metrics)

- **Coverage Calculation**: 
  - Each endpoint = 1 point (10 total points)
  - Percentage: `(earned_points / total_points) * 100`
  - Normalized for detail endpoints (e.g., `/stages/abc123` → `/stages/{id}`)
  
- **Pagination Awareness**:
  - Paginated endpoints require multiple page visits for full coverage
  - 1 page visited = 50% of endpoint (0.5 points)
  - 2 pages visited = 75% of endpoint (0.75 points)
  - 3+ pages visited = 100% of endpoint (1.0 point)
  - Pages detected via query params: `offset`, `limit`, `page`, `skip`

### 3. Session Reset
- **Trigger**: Visiting root endpoint `/`
- **Action**: Clears session and resets coverage to 0%
- **Metadata**: Root endpoint includes explanation of reset mechanism

## Coverage Endpoint

**GET /coverage** returns detailed session and coverage information:

```json
{
  "status": "success",
  "data": {
    "visitor_id": "be5096e399266dc3",
    "session_exists": true,
    "session": {
      "first_seen": "2026-02-21 19:24:18",
      "last_seen": "2026-02-21 19:26:07",
      "request_count": 4
    },
    "coverage": {
      "percentage": 17.5,
      "earned_points": 1.75,
      "total_points": 10
    },
    "missing_endpoints": [
      {"endpoint": "/greeting", "paginated": false}
    ],
    "incomplete_endpoints": [
      {
        "endpoint": "/stages",
        "pages_visited": [0, 1],
        "coverage_pct": 75.0,
        "suggestion": "Visit more pages (offset/limit params) to increase coverage"
      }
    ]
  }
}
```

**Key fields**:
- `earned_points`: Sum of partial coverage across all endpoints (0.0 - 10.0)
- `total_points`: Total available points (always 10)
- `missing_endpoints`: Endpoints not visited yet
- `incomplete_endpoints`: Paginated endpoints with <100% coverage (1-2 pages visited)

## Response Headers

Every API response includes coverage metadata:

```http
X-Session-Visitor-ID: be5096e399266dc3
X-Session-Request-Count: 4
X-Coverage-Visited: 2
X-Coverage-Total: 10
X-Coverage-Percentage: 20.0
X-Coverage-Earned-Points: 2.00
X-Coverage-Total-Points: 10
X-Coverage-Current-Endpoint: /skills
X-Coverage-Is-Relevant: true
```

## Configuration

Edit `config.yaml` to customize:

```yaml
session:
  enabled: true
  timeout_hours: 5
  log_file: logs/api_access.log
  track_coverage: true
```

## Log Format

```
2026-02-21 18:45:36 | SESSION_NEW | be5096e399266dc3 | ip=127.0.0.x | ua=curl/8.7.1
2026-02-21 18:45:36 | REQUEST | be5096e399266dc3 | GET /greeting | count=1
2026-02-21 18:45:49 | REQUEST | be5096e399266dc3 | GET /skills | count=2
2026-02-21 18:46:20 | SESSION_RESET | be5096e399266dc3 | requests=2 | coverage=20.0%
```

## Rate Limits

Increased to support exploration:
- Most endpoints: `120/minute` (was 30/minute)
- High-frequency: `200/minute` (was 60/minute)
- Search/graph: `60/minute` (was 20/minute)

## Use Cases

### LLM Agent Exploration
- Check `X-Coverage-Percentage` header to monitor exploration progress
- Systematically visit all relevant endpoints to achieve 100% coverage
- Use coverage feedback to identify missing endpoints

### Analytics
- Track which endpoints are most frequently accessed
- Identify popular exploration patterns
- Monitor unique visitors over time

### Debugging
- Review `logs/api_access.log` for request history
- Correlate errors with visitor sessions
- Analyze coverage patterns

## Example: Achieving 100% Coverage

```bash
# Reset session
curl http://localhost:8000/

# Visit all relevant endpoints
curl http://localhost:8000/greeting

# Paginated endpoints require 3+ pages for full coverage
curl "http://localhost:8000/stages?limit=10&offset=0"
curl "http://localhost:8000/stages?limit=10&offset=10"
curl "http://localhost:8000/stages?limit=10&offset=20"
curl http://localhost:8000/stages/{some-id}

curl "http://localhost:8000/oeuvre?limit=10&offset=0"
curl "http://localhost:8000/oeuvre?limit=10&offset=10"
curl "http://localhost:8000/oeuvre?limit=10&offset=20"
curl http://localhost:8000/oeuvre/{some-id}

curl "http://localhost:8000/skills?limit=10&offset=0"
curl "http://localhost:8000/skills?limit=10&offset=10"
curl "http://localhost:8000/skills?limit=10&offset=20"
curl http://localhost:8000/skills/Python

curl "http://localhost:8000/technology?limit=10&offset=0"
curl "http://localhost:8000/technology?limit=10&offset=10"
curl "http://localhost:8000/technology?limit=10&offset=20"
curl http://localhost:8000/technology/Docker

curl http://localhost:8000/tags/machine-learning

# Check final coverage (should be 100%)
curl -I http://localhost:8000/greeting | grep X-Coverage
# X-Coverage-Percentage: 100.0
# X-Coverage-Earned-Points: 10.00
# X-Coverage-Total-Points: 10
```

## Privacy

- **No PII stored**: Only anonymized IP addresses logged
- **Ephemeral sessions**: Auto-expire after timeout
- **Local storage**: Logs stored locally, not transmitted
- **User control**: Session reset available at any time via `/`
