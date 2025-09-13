# dashboard.py  -- لوحة تحكم الأدمن (يمكن لصقها داخل main.py أيضاً)
from flask import Blueprint, render_template_string, request, redirect, url_for, session, flash, current_app, jsonify
from datetime import datetime, timedelta
from functools import wraps
import math

def init_dashboard(app, links_collection):
    """
    Call this from your main module after you have a Flask `app` and a `links_collection` (pymongo collection).
    Example:
        from dashboard import init_dashboard
        init_dashboard(app, links_collection)
    """

    admin_bp = Blueprint("admin_bp", __name__, url_prefix="/admin")

    # ------------ صفحة تسجيل الدخول ------------
    @admin_bp.route("/login", methods=["GET", "POST"])
    def login():
        # ENV-based admin password
        ADMIN_PASS = app.config.get("ADMIN_PASS") or current_app.config.get("ADMIN_PASS") or ""
        if request.method == "POST":
            pw = request.form.get("password", "")
            if ADMIN_PASS and pw == ADMIN_PASS:
                session["is_admin"] = True
                # anti-CSRF token (simple)
                session["admin_token"] = str(datetime.utcnow().timestamp())
                return redirect(url_for("admin_bp.dashboard"))
            else:
                flash("كلمة المرور خاطئة.", "danger")
        return render_template_string(LOGIN_HTML)

    # ------------ تسجيل الخروج ------------
    @admin_bp.route("/logout")
    def logout():
        session.pop("is_admin", None)
        session.pop("admin_token", None)
        flash("تم تسجيل الخروج.", "info")
        return redirect(url_for("admin_bp.login"))

    # Decorator للتحقق من جلسة الأدمن
    def admin_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("is_admin"):
                return redirect(url_for("admin_bp.login"))
            return f(*args, **kwargs)
        return decorated

    # ------------ لوحة التحكم الرئيسية ------------
    @admin_bp.route("/", methods=["GET"])
    @admin_required
    def dashboard():
        # بحث + فلترة + ترقيم الصفحات
        q = request.args.get("q", "").strip()
        page = max(1, int(request.args.get("page", 1)))
        per_page = int(request.args.get("per_page", 15))

        query = {"expired": {"$ne": True}}  # نعرض الروابط غير المنتهية افتراضياً
        if q:
            # بحث بسيط بالـ file_name أو file_id
            query["$or"] = [
                {"file_name": {"$regex": q, "$options": "i"}},
                {"_id": {"$regex": q, "$options": "i"}}
            ]

        total = links_collection.count_documents(query)
        pages = max(1, math.ceil(total / per_page))
        skip = (page - 1) * per_page

        docs_cursor = links_collection.find(query).sort("expire_time", 1).skip(skip).limit(per_page)
        docs = []
        for d in docs_cursor:
            # Normalize fields for the template
            docs.append({
                "file_id": d.get("_id"),
                "file_name": d.get("file_name", "—"),
                "file_size": d.get("file_size", 0),
                "expire_time": d.get("expire_time"),
                "views": d.get("views", 0),
                "expired": d.get("expired", False),
                "uploader": d.get("uploader")
            })

        stats = {
            "total_links": links_collection.count_documents({}),
            "active_links": links_collection.count_documents({"expired": {"$ne": True}}),
            "expired_links": links_collection.count_documents({"expired": True}),
        }

        return render_template_string(DASH_HTML, docs=docs, q=q, page=page, pages=pages, per_page=per_page, stats=stats)

    # ------------ حذف رابط (POST) ------------
    @admin_bp.route("/delete", methods=["POST"])
    @admin_required
    def delete_link():
        token = request.form.get("admin_token")
        if token != session.get("admin_token"):
            flash("فشل التحقق.", "danger")
            return redirect(url_for("admin_bp.dashboard"))
        file_id = request.form.get("file_id")
        if not file_id:
            flash("لم تحدد رابط للحذف.", "warning")
            return redirect(url_for("admin_bp.dashboard"))
        # حذف من DB
        links_collection.delete_one({"_id": file_id})
        flash(f"تم حذف الرابط {file_id}", "success")
        return redirect(url_for("admin_bp.dashboard"))

    # ------------ تمديد الصلاحية (POST) ------------
    @admin_bp.route("/extend", methods=["POST"])
    @admin_required
    def extend_link():
        token = request.form.get("admin_token")
        if token != session.get("admin_token"):
            flash("فشل التحقق.", "danger")
            return redirect(url_for("admin_bp.dashboard"))
        file_id = request.form.get("file_id")
        days = int(request.form.get("days", 1))
        if not file_id:
            flash("لم تحدد رابط للتمديد.", "warning")
            return redirect(url_for("admin_bp.dashboard"))
        new_expire = datetime.utcnow() + timedelta(days=days)
        links_collection.update_one({"_id": file_id}, {"$set": {"expire_time": new_expire, "expired": False}})
        flash(f"تم تمديد صلاحية الرابط {file_id} لمدّة {days} يوم.", "success")
        return redirect(url_for("admin_bp.dashboard"))

    # ------------ تعطيل / تفعيل رابط (POST) ------------
    @admin_bp.route("/toggle", methods=["POST"])
    @admin_required
    def toggle_link():
        token = request.form.get("admin_token")
        if token != session.get("admin_token"):
            flash("فشل التحقق.", "danger")
            return redirect(url_for("admin_bp.dashboard"))
        file_id = request.form.get("file_id")
        if not file_id:
            flash("لم تحدد رابط.", "warning")
            return redirect(url_for("admin_bp.dashboard"))
        doc = links_collection.find_one({"_id": file_id})
        if not doc:
            flash("الرابط غير موجود.", "warning")
            return redirect(url_for("admin_bp.dashboard"))
        new_state = not doc.get("expired", False)
        links_collection.update_one({"_id": file_id}, {"$set": {"expired": new_state}})
        flash(f"تم {'تعطيل' if new_state else 'تفعيل'} الرابط {file_id}.", "success")
        return redirect(url_for("admin_bp.dashboard"))

    # ------------ API خفيف لإرجاع بيانات رابط (JSON) ------------
    @admin_bp.route("/api/link/<file_id>", methods=["GET"])
    @admin_required
    def api_link(file_id):
        doc = links_collection.find_one({"_id": file_id})
        if not doc:
            return jsonify({"ok": False, "error": "not_found"}), 404
        # تحويل التاريخ إلى iso
        doc["expire_time"] = doc.get("expire_time").isoformat() if doc.get("expire_time") else None
        return jsonify({"ok": True, "doc": doc})

    # ===== تسجيل الـ Blueprint في التطبيق =====
    app.register_blueprint(admin_bp)


# ==================== قوالب HTML مضمنة (Bootstrap بسيط) ====================

LOGIN_HTML = """
<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>تسجيل دخول الأدمن</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet">
</head>
<body class="bg-light">
  <div class="container py-5">
    <div class="row justify-content-center">
      <div class="col-md-6">
        <div class="card shadow-sm">
          <div class="card-body">
            <h4 class="card-title mb-4">لوحة التحكم - تسجيل الدخول</h4>
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                {% for cat, msg in messages %}
                  <div class="alert alert-{{cat}}">{{msg}}</div>
                {% endfor %}
              {% endif %}
            {% endwith %}
            <form method="post">
              <div class="mb-3">
                <label class="form-label">كلمة المرور</label>
                <input type="password" name="password" class="form-control" required>
              </div>
              <button class="btn btn-primary">دخول</button>
            </form>
          </div>
        </div>
        <p class="text-muted mt-2">استخدام مظهر بسيط للوحة - يمكنك تخصيصها لاحقًا.</p>
      </div>
    </div>
  </div>
</body>
</html>
"""

DASH_HTML = """
<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>لوحة تحكم الأدمن</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet">
  <style>
    body { background:#f7fafc; }
    .small-muted { font-size:0.9rem; color:#6c757d; }
    pre { white-space: pre-wrap; }
  </style>
</head>
<body>
  <nav class="navbar navbar-expand-lg navbar-light bg-white border-bottom">
    <div class="container">
      <a class="navbar-brand" href="{{ url_for('admin_bp.dashboard') }}">Dashboard</a>
      <div>
        <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('admin_bp.logout') }}">تسجيل خروج</a>
      </div>
    </div>
  </nav>

  <div class="container py-4">
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for cat, msg in messages %}
          <div class="alert alert-{{cat}}">{{msg}}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <div class="row mb-3">
      <div class="col-md-8">
        <form class="row g-2" method="get">
          <div class="col-auto">
            <input class="form-control" name="q" placeholder="بحث باسم الملف أو id..." value="{{ q }}">
          </div>
          <div class="col-auto">
            <select name="per_page" class="form-select">
              <option value="10" {% if per_page==10 %}selected{% endif %}>10</option>
              <option value="15" {% if per_page==15 %}selected{% endif %}>15</option>
              <option value="30" {% if per_page==30 %}selected{% endif %}>30</option>
            </select>
          </div>
          <div class="col-auto">
            <button class="btn btn-primary">بحث</button>
          </div>
        </form>
      </div>
      <div class="col-md-4 text-end">
        <div class="small-muted">إجمالي الروابط: {{ stats.total_links }} — نشطة: {{ stats.active_links }} — منتهية: {{ stats.expired_links }}</div>
      </div>
    </div>

    <div class="card mb-3">
      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-striped m-0">
            <thead>
              <tr>
                <th>المعرف</th>
                <th>الاسم</th>
                <th>الحجم (MB)</th>
                <th>المشاهدات</th>
                <th>المنتهية</th>
                <th>تنتهي في</th>
                <th>أدوات</th>
              </tr>
            </thead>
            <tbody>
              {% for d in docs %}
                <tr>
                  <td><pre style="margin:0">{{ d.file_id }}</pre></td>
                  <td>{{ d.file_name }}</td>
                  <td>{{ '%.2f' % (d.file_size / (1024*1024) ) }}</td>
                  <td>{{ d.views }}</td>
                  <td>{% if d.expired %}<span class="badge bg-danger">منتهية</span>{% else %}<span class="badge bg-success">نشطة</span>{% endif %}</td>
                  <td>{{ d.expire_time.strftime('%Y-%m-%d %H:%M:%S') if d.expire_time else '—' }}</td>
                  <td>
                    <form method="post" action="{{ url_for('admin_bp.delete_link') }}" style="display:inline">
                      <input type="hidden" name="file_id" value="{{ d.file_id }}">
                      <input type="hidden" name="admin_token" value="{{ session.admin_token }}">
                      <button class="btn btn-sm btn-danger" onclick="return confirm('حذف الرابط؟');">حذف</button>
                    </form>

                    <form method="post" action="{{ url_for('admin_bp.extend_link') }}" style="display:inline">
                      <input type="hidden" name="file_id" value="{{ d.file_id }}">
                      <input type="hidden" name="admin_token" value="{{ session.admin_token }}">
                      <select name="days" class="form-select form-select-sm d-inline" style="width:90px; display:inline-block">
                        <option value="1">+1 يوم</option>
                        <option value="3">+3 أيام</option>
                        <option value="7">+7 أيام</option>
                        <option value="30">+30 يوم</option>
                      </select>
                      <button class="btn btn-sm btn-outline-primary">تمديد</button>
                    </form>

                    <form method="post" action="{{ url_for('admin_bp.toggle_link') }}" style="display:inline">
                      <input type="hidden" name="file_id" value="{{ d.file_id }}">
                      <input type="hidden" name="admin_token" value="{{ session.admin_token }}">
                      <button class="btn btn-sm btn-outline-warning">تبديل حالة</button>
                    </form>
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <nav aria-label="pagination">
      <ul class="pagination">
        {% for p in range(1, pages+1) %}
          <li class="page-item {% if p==page %}active{% endif %}">
            <a class="page-link" href="?q={{ q }}&page={{ p }}&per_page={{ per_page }}">{{ p }}</a>
          </li>
        {% endfor %}
      </ul>
    </nav>

  </div>
</body>
</html>
"""
