"""
app/session_tracker.py — Session tracking and coverage monitoring with SQLite
===============================================================================
Tracks API client sessions based on anonymized IP + User-Agent hash.
Monitors endpoint coverage for metric-relevant routes with pagination support.

Purpose:
  - Log caller activity (anonymized IP, user agent, endpoints)
  - Track which endpoints a caller has explored (including pagination)
  - Calculate coverage percentage for metric-relevant endpoints
  - Provide feedback to LLM agents about their exploration progress
  - Persist sessions in SQLite for durability

Key Features:
  - Session ID: hash(anonymized_ip + user_agent)
  - Sessions expire after configurable timeout (default: 5 hours)
  - Sessions reset when root endpoint (/) is hit
  - Coverage tracking with pagination awareness
  - SQLite persistence for sessions and coverage data
  - Configurable relevant endpoints (all count equally - 1 point each)

Pagination Handling:
  - Paginated endpoints (/stages, /skills, /technology, etc.) give partial coverage
  - First page = 50% of endpoint (0.5 points)
  - Two pages = 75% of endpoint (0.75 points)
  - Three+ pages = 100% of endpoint (1.0 point)
  - Non-paginated endpoints = 100% on first visit (1.0 point)
  
Coverage Calculation:
  - Each endpoint counts equally (1 point)
  - Total points = number of relevant endpoints
  - Earned points = sum of per-endpoint completion (0.0 - 1.0 each)
  - Percentage = (earned_points / total_points) * 100
"""

import hashlib
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Tuple, Set
from urllib.parse import parse_qs, urlparse


# ─────────────────────────────────────────────────────────────────────────────
# SQLITE SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    visitor_id TEXT PRIMARY KEY,
    anonymized_ip TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    first_seen TIMESTAMP NOT NULL,
    last_seen TIMESTAMP NOT NULL,
    request_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_last_seen ON sessions(last_seen);

CREATE TABLE IF NOT EXISTS session_coverage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visitor_id TEXT NOT NULL,
    endpoint_pattern TEXT NOT NULL,
    page_number INTEGER DEFAULT 0,
    timestamp TIMESTAMP NOT NULL,
    FOREIGN KEY (visitor_id) REFERENCES sessions(visitor_id) ON DELETE CASCADE,
    UNIQUE (visitor_id, endpoint_pattern, page_number)
);

CREATE INDEX IF NOT EXISTS idx_coverage_visitor ON session_coverage(visitor_id);

CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visitor_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    FOREIGN KEY (visitor_id) REFERENCES sessions(visitor_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_log_visitor ON request_log(visitor_id);
CREATE INDEX IF NOT EXISTS idx_log_timestamp ON request_log(timestamp);
"""


# ─────────────────────────────────────────────────────────────────────────────
# SESSION TRACKER
# ─────────────────────────────────────────────────────────────────────────────

class SessionTracker:
    """
    SQLite-backed session tracker with pagination-aware coverage monitoring.
    
    Manages all active sessions and provides coverage statistics.
    Sessions are automatically cleaned up when expired.
    """
    
    def __init__(
        self,
        timeout_hours: float = 5.0,
        log_file: Optional[str] = None,
        db_path: str = "db/sessions.db",
        relevant_endpoints: Optional[Dict[str, dict]] = None
    ):
        """
        Initialize session tracker with SQLite backend.
        
        Args:
            timeout_hours: Session expiry timeout in hours
            log_file: Path to log file for session activity
            db_path: Path to SQLite database
            relevant_endpoints: Dict of {endpoint_pattern: {weight, paginated}}
        """
        self.timeout_hours = timeout_hours
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        
        # Relevant endpoints configuration
        self.relevant_endpoints = relevant_endpoints or {}
        
        # Initialize database
        self._init_db()
        
        # Setup logging
        self.logger = logging.getLogger("session_tracker")
        self.logger.setLevel(logging.INFO)
        
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_path)
            handler.setFormatter(logging.Formatter(
                '%(asctime)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            ))
            self.logger.addHandler(handler)
    
    def _init_db(self):
        """Initialize SQLite database with schema."""
        conn = sqlite3.connect(str(self.db_path))
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        conn.close()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get SQLite connection with row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")  # Enable CASCADE deletes
        return conn
    
    def _cleanup_expired(self, conn: sqlite3.Connection):
        """Remove expired sessions."""
        cutoff = datetime.now() - timedelta(hours=self.timeout_hours)
        expired = conn.execute(
            "SELECT visitor_id, request_count FROM sessions WHERE last_seen < ?",
            (cutoff,)
        ).fetchall()
        
        for row in expired:
            visitor_id = row['visitor_id']
            request_count = row['request_count']
            
            # Delete session (cascades to coverage and logs)
            conn.execute("DELETE FROM sessions WHERE visitor_id = ?", (visitor_id,))
            
            self.logger.info(
                f"SESSION_EXPIRED | {visitor_id} | requests={request_count}"
            )
        
        conn.commit()
    
    def track_request(
        self,
        ip_address: str,
        user_agent: str,
        endpoint: str,
        method: str = "GET",
        query_params: Optional[Dict] = None
    ) -> dict:
        """
        Track a request and return coverage metadata.
        
        Args:
            ip_address: Client IP address (will be anonymized)
            user_agent: User-Agent header
            endpoint: Request endpoint path
            method: HTTP method
            query_params: Parsed query parameters (for pagination detection)
        
        Returns:
            Coverage metadata dictionary
        """
        anonymized_ip = _anonymize_ip(ip_address)
        visitor_id = _generate_visitor_id(anonymized_ip, user_agent)
        now = datetime.now()
        
        with self.lock:
            conn = self._get_conn()
            try:
                self._cleanup_expired(conn)
                
                # Get or create session
                session = conn.execute(
                    "SELECT * FROM sessions WHERE visitor_id = ?",
                    (visitor_id,)
                ).fetchone()
                
                if not session:
                    conn.execute(
                        "INSERT INTO sessions (visitor_id, anonymized_ip, user_agent, first_seen, last_seen, request_count) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (visitor_id, anonymized_ip, user_agent, now, now, 0)
                    )
                    self.logger.info(
                        f"SESSION_NEW | {visitor_id} | ip={anonymized_ip} | ua={user_agent[:80]}"
                    )
                
                # Update session
                conn.execute(
                    "UPDATE sessions SET last_seen = ?, request_count = request_count + 1 "
                    "WHERE visitor_id = ?",
                    (now, visitor_id)
                )
                
                # Get updated request count
                request_count = conn.execute(
                    "SELECT request_count FROM sessions WHERE visitor_id = ?",
                    (visitor_id,)
                ).fetchone()['request_count']
                
                # Track coverage
                normalized = _normalize_endpoint(endpoint, list(self.relevant_endpoints.keys()))
                page_number = _extract_page_number(query_params) if query_params else 0
                
                if normalized:
                    conn.execute(
                        "INSERT OR IGNORE INTO session_coverage (visitor_id, endpoint_pattern, page_number, timestamp) "
                        "VALUES (?, ?, ?, ?)",
                        (visitor_id, normalized, page_number, now)
                    )
                
                # Log request
                conn.execute(
                    "INSERT INTO request_log (visitor_id, endpoint, method, timestamp) "
                    "VALUES (?, ?, ?, ?)",
                    (visitor_id, endpoint, method, now)
                )
                
                self.logger.info(
                    f"REQUEST | {visitor_id} | {method} {endpoint} | count={request_count}"
                )
                
                conn.commit()
                
                # Calculate coverage
                coverage = self._calculate_coverage(conn, visitor_id)
                
                return {
                    "visitor_id": visitor_id,
                    "session": {
                        "first_seen": session['first_seen'] if session else now.isoformat(),
                        "last_seen": now.isoformat(),
                        "request_count": request_count,
                        "timeout_hours": self.timeout_hours,
                    },
                    "coverage": coverage
                }
            
            finally:
                conn.close()
    
    def _calculate_coverage(self, conn: sqlite3.Connection, visitor_id: str) -> dict:
        """
        Calculate coverage percentage with pagination awareness.
        Each endpoint counts equally (1 point). Pagination affects per-endpoint completion.
        
        Args:
            conn: SQLite connection
            visitor_id: Session visitor ID
        
        Returns:
            Coverage dict with visited, total, percentage, breakdown
        """
        visited_rows = conn.execute(
            "SELECT endpoint_pattern, page_number FROM session_coverage WHERE visitor_id = ?",
            (visitor_id,)
        ).fetchall()
        
        # Group by endpoint pattern
        visited_map: Dict[str, Set[int]] = {}
        for row in visited_rows:
            pattern = row['endpoint_pattern']
            page = row['page_number']
            if pattern not in visited_map:
                visited_map[pattern] = set()
            visited_map[pattern].add(page)
        
        # Calculate simple coverage (each endpoint = 1 point)
        total_points = len(self.relevant_endpoints)
        earned_points = 0.0
        
        breakdown = []
        for pattern, config in self.relevant_endpoints.items():
            is_paginated = config.get('paginated', False)
            visited_pages = visited_map.get(pattern, set())
            
            if not visited_pages:
                # Not visited
                coverage_pct = 0.0
                earned = 0.0
            elif not is_paginated:
                # Non-paginated: full point on first visit
                coverage_pct = 100.0
                earned = 1.0
            else:
                # Paginated: partial points based on pages visited
                num_pages = len(visited_pages)
                if num_pages == 1:
                    coverage_pct = 50.0
                    earned = 0.5
                elif num_pages == 2:
                    coverage_pct = 75.0
                    earned = 0.75
                else:
                    coverage_pct = 100.0
                    earned = 1.0
            
            earned_points += earned
            
            breakdown.append({
                "endpoint": pattern,
                "visited": len(visited_pages) > 0,
                "pages_visited": sorted(list(visited_pages)) if visited_pages else [],
                "coverage_pct": round(coverage_pct, 1),
                "earned": round(earned, 2),
                "paginated": is_paginated,
            })
        
        overall_percentage = (earned_points / total_points * 100.0) if total_points > 0 else 0.0
        
        return {
            "visited_count": len(visited_map),
            "total_count": len(self.relevant_endpoints),
            "percentage": round(overall_percentage, 2),
            "earned_points": round(earned_points, 2),
            "total_points": total_points,
            "breakdown": breakdown,
        }
    
    def get_coverage(self, ip_address: str, user_agent: str) -> dict:
        """
        Get detailed coverage report for a session.
        
        Args:
            ip_address: Client IP address
            user_agent: User-Agent header
        
        Returns:
            Coverage report with missing endpoints
        """
        anonymized_ip = _anonymize_ip(ip_address)
        visitor_id = _generate_visitor_id(anonymized_ip, user_agent)
        
        with self.lock:
            conn = self._get_conn()
            try:
                session = conn.execute(
                    "SELECT * FROM sessions WHERE visitor_id = ?",
                    (visitor_id,)
                ).fetchone()
                
                if not session:
                    return {
                        "visitor_id": visitor_id,
                        "session_exists": False,
                        "message": "No active session. Call any endpoint to start tracking.",
                    }
                
                coverage = self._calculate_coverage(conn, visitor_id)
                
                # Identify missing endpoints
                missing = []
                incomplete = []
                
                for item in coverage['breakdown']:
                    if not item['visited']:
                        missing.append({
                            "endpoint": item['endpoint'],
                            "paginated": item['paginated'],
                        })
                    elif item['paginated'] and item['coverage_pct'] < 100.0:
                        incomplete.append({
                            "endpoint": item['endpoint'],
                            "pages_visited": item['pages_visited'],
                            "coverage_pct": item['coverage_pct'],
                            "suggestion": "Visit more pages (offset/limit params) to increase coverage"
                        })
                
                return {
                    "visitor_id": visitor_id,
                    "session_exists": True,
                    "session": {
                        "first_seen": session['first_seen'],
                        "last_seen": session['last_seen'],
                        "request_count": session['request_count'],
                    },
                    "coverage": {
                        "percentage": coverage['percentage'],
                        "earned_points": coverage['earned_points'],
                        "total_points": coverage['total_points'],
                    },
                    "missing_endpoints": missing,
                    "incomplete_endpoints": incomplete,
                    "breakdown": coverage['breakdown'],
                }
            
            finally:
                conn.close()
    
    def reset_session(self, ip_address: str, user_agent: str):
        """
        Reset a session (called when root endpoint is hit).
        
        Args:
            ip_address: Client IP address
            user_agent: User-Agent header
        """
        anonymized_ip = _anonymize_ip(ip_address)
        visitor_id = _generate_visitor_id(anonymized_ip, user_agent)
        
        with self.lock:
            conn = self._get_conn()
            try:
                session = conn.execute(
                    "SELECT * FROM sessions WHERE visitor_id = ?",
                    (visitor_id,)
                ).fetchone()
                
                if session:
                    coverage = self._calculate_coverage(conn, visitor_id)
                    self.logger.info(
                        f"SESSION_RESET | {visitor_id} | "
                        f"requests={session['request_count']} | "
                        f"coverage={coverage['percentage']:.1f}%"
                    )
                    
                    # Delete session (cascades to coverage and logs)
                    conn.execute("DELETE FROM sessions WHERE visitor_id = ?", (visitor_id,))
                    conn.commit()
            
            finally:
                conn.close()
    
    def get_stats(self) -> dict:
        """Get overall tracker statistics."""
        with self.lock:
            conn = self._get_conn()
            try:
                self._cleanup_expired(conn)
                
                active_sessions = conn.execute(
                    "SELECT COUNT(*) as count FROM sessions"
                ).fetchone()['count']
                
                return {
                    "active_sessions": active_sessions,
                    "relevant_endpoints_count": len(self.relevant_endpoints),
                    "total_points": len(self.relevant_endpoints),
                    "timeout_hours": self.timeout_hours,
                }
            
            finally:
                conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _anonymize_ip(ip: str) -> str:
    """
    Remove last octet from IP address.
    
    Args:
        ip: Full IP address (e.g., "192.168.1.100")
    
    Returns:
        Anonymized IP (e.g., "192.168.1.x")
    """
    parts = ip.split(".")
    if len(parts) == 4:  # IPv4
        return f"{parts[0]}.{parts[1]}.{parts[2]}.x"
    # IPv6 - remove last segment
    if ":" in ip:
        parts = ip.split(":")
        return ":".join(parts[:-1]) + ":x"
    return "unknown.x"


def _generate_visitor_id(anonymized_ip: str, user_agent: str) -> str:
    """
    Generate unique visitor ID from anonymized IP and user agent.
    
    Args:
        anonymized_ip: IP with last octet removed
        user_agent: User-Agent header
    
    Returns:
        SHA256 hash (first 16 chars)
    """
    combined = f"{anonymized_ip}|{user_agent}"
    hash_obj = hashlib.sha256(combined.encode('utf-8'))
    return hash_obj.hexdigest()[:16]


def _normalize_endpoint(path: str, relevant_patterns: List[str]) -> Optional[str]:
    """
    Normalize endpoint path to match relevant endpoint patterns.
    
    Converts specific IDs to generic patterns:
      /stages/abc123 → /stages/{id}
      /skills/Python → /skills/{name}
    
    Args:
        path: Request path
        relevant_patterns: List of relevant endpoint patterns
    
    Returns:
        Normalized pattern or None if not relevant
    """
    # Remove query params
    if "?" in path:
        path = path.split("?")[0]
    
    # Exact matches
    if path in relevant_patterns:
        return path
    
    # Pattern matches
    if path.startswith("/stages/") and "/stages/{id}" in relevant_patterns:
        return "/stages/{id}"
    elif path.startswith("/oeuvre/") and "/oeuvre/{id}" in relevant_patterns:
        return "/oeuvre/{id}"
    elif path.startswith("/skills/") and "/skills/{name}" in relevant_patterns:
        return "/skills/{name}"
    elif path.startswith("/technology/") and "/technology/{name}" in relevant_patterns:
        return "/technology/{name}"
    elif path.startswith("/tags/") and "/tags/{tag_name}" in relevant_patterns:
        return "/tags/{tag_name}"
    
    return None


def _extract_page_number(query_params: Dict) -> int:
    """
    Extract page number from query parameters.
    
    Looks for common pagination params:
      - offset (converts to page number assuming limit=20)
      - page
      - skip
    
    Args:
        query_params: Parsed query parameters
    
    Returns:
        Page number (0-indexed)
    """
    # Check for offset
    if 'offset' in query_params:
        try:
            offset = int(query_params['offset'])
            limit = int(query_params.get('limit', 20))
            return offset // limit
        except (ValueError, ZeroDivisionError):
            return 0
    
    # Check for page
    if 'page' in query_params:
        try:
            return max(0, int(query_params['page']) - 1)  # Convert 1-indexed to 0-indexed
        except ValueError:
            return 0
    
    # Check for skip (similar to offset)
    if 'skip' in query_params:
        try:
            skip = int(query_params['skip'])
            limit = int(query_params.get('limit', 20))
            return skip // limit
        except (ValueError, ZeroDivisionError):
            return 0
    
    return 0

    return 0

