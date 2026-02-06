# -*- coding: utf-8 -*-
"""
Server Monitoring va Health Check System
Digital Ocean serverda sayt holatini kuzatish
"""

import logging
import os
import psutil
import time
from datetime import datetime, timedelta
from functools import wraps
from flask import jsonify, render_template, request, session, redirect, url_for
from sqlalchemy import text

logger = logging.getLogger(__name__)

class ServerMonitor:
    """Server resurslarini monitoring qilish"""

    @staticmethod
    def get_cpu_usage():
        """CPU ishlatilishi (%)"""
        return psutil.cpu_percent(interval=1)

    @staticmethod
    def get_memory_usage():
        """RAM ishlatilishi"""
        memory = psutil.virtual_memory()
        return {
            'total': round(memory.total / (1024**3), 2),  # GB
            'used': round(memory.used / (1024**3), 2),    # GB
            'percent': memory.percent
        }

    @staticmethod
    def get_disk_usage():
        """Disk ishlatilishi"""
        disk = psutil.disk_usage('/')
        return {
            'total': round(disk.total / (1024**3), 2),    # GB
            'used': round(disk.used / (1024**3), 2),      # GB
            'percent': disk.percent
        }

    @staticmethod
    def get_network_stats():
        """Network statistikasi"""
        net = psutil.net_io_counters()
        return {
            'bytes_sent': round(net.bytes_sent / (1024**2), 2),    # MB
            'bytes_recv': round(net.bytes_recv / (1024**2), 2),    # MB
        }


class DatabaseMonitor:
    """Database holatini monitoring qilish"""

    def __init__(self, db):
        self.db = db

    def check_connection(self):
        """Database connection tekshirish"""
        try:
            self.db.session.execute(text("SELECT 1")).scalar()
            return {'status': 'ok', 'connected': True}
        except Exception as e:
            logger.error(f"Database connection error: {e}")
            return {'status': 'error', 'connected': False, 'error': str(e)}

    def get_connection_count(self):
        """Active connection sonini olish"""
        try:
            query = text("""
                SELECT count(*) as active_connections
                FROM pg_stat_activity
                WHERE state = 'active'
            """)
            result = self.db.session.execute(query).scalar()
            return result
        except Exception as e:
            logger.error(f"Error getting connection count: {e}")
            return None

    def get_database_size(self):
        """Database hajmini olish"""
        try:
            query = text("""
                SELECT pg_size_pretty(pg_database_size(current_database())) as size
            """)
            result = self.db.session.execute(query).scalar()
            return result
        except Exception as e:
            logger.error(f"Error getting database size: {e}")
            return None

    def get_slow_queries(self, threshold_seconds=5):
        """Sekin ishlayotgan querylarni topish"""
        try:
            query = text("""
                SELECT
                    pid,
                    now() - query_start as duration,
                    query,
                    state
                FROM pg_stat_activity
                WHERE state != 'idle'
                AND now() - query_start > interval ':threshold seconds'
                ORDER BY duration DESC
            """)
            result = self.db.session.execute(
                query,
                {'threshold': threshold_seconds}
            ).fetchall()
            return [dict(row._mapping) for row in result]
        except Exception as e:
            logger.error(f"Error getting slow queries: {e}")
            return []


class ApplicationMonitor:
    """Ilova holatini monitoring qilish"""

    def __init__(self, db):
        self.db = db
        self.start_time = datetime.now()

    def get_uptime(self):
        """Ilova qancha vaqt ishlayapti"""
        uptime = datetime.now() - self.start_time
        return {
            'seconds': uptime.total_seconds(),
            'formatted': str(uptime).split('.')[0]  # HH:MM:SS formatda
        }

    def get_recent_errors(self, hours=1):
        """Oxirgi xatolarni olish (log faylidan)"""
        try:
            log_file = 'logs/error.log'
            if not os.path.exists(log_file):
                return []

            # Oxirgi 100 qatorni o'qish
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()[-100:]

            errors = []
            for line in lines:
                if 'ERROR' in line or 'CRITICAL' in line:
                    errors.append(line.strip())

            return errors[-20:]  # Oxirgi 20 ta xato
        except Exception as e:
            logger.error(f"Error reading error log: {e}")
            return []

    def get_request_stats(self):
        """Access log statistikasi"""
        try:
            log_file = 'logs/access.log'
            if not os.path.exists(log_file):
                return {'total': 0, 'recent': 0}

            # Oxirgi 1000 qatorni o'qish
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()[-1000:]

            total = len(lines)

            # Oxirgi 5 daqiqadagi requestlar
            recent = min(100, total)  # Soddalashtirilgan hisoblash

            return {'total': total, 'recent_5min': recent}
        except Exception as e:
            logger.error(f"Error reading access log: {e}")
            return {'total': 0, 'recent_5min': 0}


def setup_monitoring_routes(app, db):
    """Monitoring route'larni qo'shish"""

    server_monitor = ServerMonitor()
    db_monitor = DatabaseMonitor(db)
    app_monitor = ApplicationMonitor(db)

    @app.route('/api/monitoring/health')
    def monitoring_health_check():
        """Monitoring health check - load balancer uchun"""
        try:
            # Database tekshirish
            db.session.execute(text("SELECT 1"))
            return jsonify({'status': 'healthy'}), 200
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return jsonify({'status': 'unhealthy', 'error': str(e)}), 503

    @app.route('/monitoring/status')
    def monitoring_status():
        """To'liq monitoring ma'lumotlari - API endpoint"""

        # Admin tekshiruvi (login qilgan bo'lishi kerak)
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401

        try:
            status = {
                'timestamp': datetime.now().isoformat(),
                'server': {
                    'cpu_percent': server_monitor.get_cpu_usage(),
                    'memory': server_monitor.get_memory_usage(),
                    'disk': server_monitor.get_disk_usage(),
                    'network': server_monitor.get_network_stats()
                },
                'database': {
                    'connection': db_monitor.check_connection(),
                    'active_connections': db_monitor.get_connection_count(),
                    'size': db_monitor.get_database_size(),
                    'slow_queries': db_monitor.get_slow_queries()
                },
                'application': {
                    'uptime': app_monitor.get_uptime(),
                    'recent_errors': app_monitor.get_recent_errors(),
                    'requests': app_monitor.get_request_stats()
                }
            }

            return jsonify(status), 200
        except Exception as e:
            logger.error(f"Monitoring status error: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/monitoring/dashboard')
    def monitoring_dashboard():
        """Monitoring dashboard - HTML page"""

        # Admin tekshiruvi
        if 'user_id' not in session:
            return redirect(url_for('login_page'))

        return render_template('monitoring_dashboard.html')

    @app.route('/monitoring/logs/<log_type>')
    def view_logs(log_type):
        """Log fayllarni ko'rish"""

        # Admin tekshiruvi
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401

        allowed_logs = ['access', 'error', 'app']
        if log_type not in allowed_logs:
            return jsonify({'error': 'Invalid log type'}), 400

        try:
            log_file = f'logs/{log_type}.log'
            if not os.path.exists(log_file):
                return jsonify({'error': 'Log file not found'}), 404

            # Oxirgi N qatorni o'qish
            lines = int(request.args.get('lines', 100))
            with open(log_file, 'r', encoding='utf-8') as f:
                content = f.readlines()[-lines:]

            return jsonify({
                'log_type': log_type,
                'lines': len(content),
                'content': ''.join(content)
            }), 200
        except Exception as e:
            logger.error(f"Error reading log {log_type}: {e}")
            return jsonify({'error': str(e)}), 500


# Error tracking decorator
def track_errors(func):
    """Funksiya xatolarini tracking qilish"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(
                f"Error in {func.__name__}: {str(e)}",
                exc_info=True,
                extra={
                    'function': func.__name__,
                    'args': str(args)[:100],
                    'kwargs': str(kwargs)[:100]
                }
            )
            raise
    return wrapper
