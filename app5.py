import streamlit as st
import sqlite3
import hashlib
import pandas as pd
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, available_timezones
from streamlit_autorefresh import st_autorefresh
from streamlit_js_eval import streamlit_js_eval

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
st.set_page_config(page_title="ChatApp", page_icon="💬", layout="wide")
DB_PATH = "chatapp.db"
SALT = "chatapp_static_salt_v1"  # not a substitute for per-user salts in production

COMMON_TIMEZONES = [
    "UTC", "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Asia/Tokyo",
    "Asia/Shanghai", "Asia/Karachi", "Asia/Dhaka", "Asia/Jakarta",
    "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Moscow",
    "Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos",
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Sao_Paulo", "America/Mexico_City",
    "Australia/Sydney", "Australia/Perth", "Pacific/Auckland",
]
ALL_TIMEZONES = sorted(available_timezones())


def get_owner_username() -> str:
    """The app owner's username, configured via Streamlit secrets (OWNER_USERNAME).
    Falls back to 'admin' if no secret is set."""
    try:
        return st.secrets.get("OWNER_USERNAME", "admin")
    except Exception:
        return "admin"


def is_owner(username: str) -> bool:
    return username is not None and username == get_owner_username()


# ---------------------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------------------
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TEXT,
            timezone TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            receiver TEXT NOT NULL,
            content TEXT,
            media_data BLOB,
            media_name TEXT,
            media_type TEXT,
            timestamp TEXT
        )
        """
    )
    # Lightweight migration in case an older DB file is present without the
    # timezone column.
    c.execute("PRAGMA table_info(users)")
    cols = [row[1] for row in c.fetchall()]
    if "timezone" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN timezone TEXT")
    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    return hashlib.sha256((SALT + password).encode()).hexdigest()


def register_user(username: str, password: str):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO users (username, password_hash, created_at, timezone) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), datetime.now(timezone.utc).isoformat(), None),
        )
        conn.commit()
        return True, "Account created successfully. Please log in."
    except sqlite3.IntegrityError:
        return False, "That username is already taken."
    finally:
        conn.close()


def authenticate_user(username: str, password: str) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    return row is not None and row[0] == hash_password(password)


def change_password(username: str, new_password: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET password_hash=? WHERE username=?", (hash_password(new_password), username))
    conn.commit()
    conn.close()


def get_all_users(exclude: str = None):
    conn = get_conn()
    c = conn.cursor()
    if exclude:
        c.execute("SELECT username FROM users WHERE username != ? ORDER BY username", (exclude,))
    else:
        c.execute("SELECT username FROM users ORDER BY username")
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows


def get_user_timezone(username: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT timezone FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def set_user_timezone(username: str, tz_name: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET timezone=? WHERE username=?", (tz_name, username))
    conn.commit()
    conn.close()


def send_message(sender, receiver, content=None, media_data=None, media_name=None, media_type=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO messages (sender, receiver, content, media_data, media_name, media_type, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (sender, receiver, content, media_data, media_name, media_type, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_messages(user1, user2):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """SELECT id, sender, receiver, content, media_data, media_name, media_type, timestamp
           FROM messages
           WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?)
           ORDER BY id ASC""",
        (user1, user2, user2, user1),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def delete_message(msg_id: int, requester: str):
    """Only the original sender may delete their own message/media."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM messages WHERE id=? AND sender=?", (msg_id, requester))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# ANALYTICS (owner only)
# ---------------------------------------------------------------------------
def get_analytics():
    conn = get_conn()
    total_users = pd.read_sql_query("SELECT COUNT(*) AS n FROM users", conn).iloc[0]["n"]
    total_messages = pd.read_sql_query("SELECT COUNT(*) AS n FROM messages", conn).iloc[0]["n"]
    total_media = pd.read_sql_query(
        "SELECT COUNT(*) AS n FROM messages WHERE media_data IS NOT NULL", conn
    ).iloc[0]["n"]
    storage_bytes = pd.read_sql_query(
        "SELECT COALESCE(SUM(LENGTH(media_data)),0) AS b FROM messages", conn
    ).iloc[0]["b"]
    users_df = pd.read_sql_query("SELECT username, created_at FROM users ORDER BY created_at", conn)
    top_senders = pd.read_sql_query(
        "SELECT sender, COUNT(*) AS messages FROM messages GROUP BY sender ORDER BY messages DESC LIMIT 10",
        conn,
    )
    msgs_df = pd.read_sql_query("SELECT timestamp FROM messages", conn)
    conn.close()

    daily_counts = pd.Series(dtype=int)
    if not msgs_df.empty:
        msgs_df["date"] = pd.to_datetime(msgs_df["timestamp"]).dt.date
        daily_counts = msgs_df.groupby("date").size()

    return {
        "total_users": int(total_users),
        "total_messages": int(total_messages),
        "total_media": int(total_media),
        "storage_bytes": int(storage_bytes),
        "users_df": users_df,
        "top_senders": top_senders,
        "daily_counts": daily_counts,
    }


def dashboard_page():
    st.title("📊 Owner Analytics Dashboard")
    st.caption(f"Logged in as owner: **{st.session_state.username}**")

    data = get_analytics()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Users", data["total_users"])
    c2.metric("Total Messages", data["total_messages"])
    c3.metric("Media Shared", data["total_media"])
    c4.metric("Storage Used", f"{data['storage_bytes'] / (1024*1024):.1f} MB")

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Messages per day (last 14 days)")
        if not data["daily_counts"].empty:
            recent = data["daily_counts"].tail(14)
            st.bar_chart(recent)
        else:
            st.info("No messages yet.")

    with col2:
        st.subheader("Most active users")
        if not data["top_senders"].empty:
            st.bar_chart(data["top_senders"].set_index("sender"))
        else:
            st.info("No messages yet.")

    st.divider()
    st.subheader("All registered users")
    st.dataframe(data["users_df"], use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# UI HELPERS
# ---------------------------------------------------------------------------
def format_time(ts: str, tz_name: str = "UTC") -> str:
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            local_dt = dt.astimezone(ZoneInfo(tz_name))
        except Exception:
            local_dt = dt.astimezone(timezone.utc)
        return local_dt.strftime("%d %b, %I:%M %p")
    except Exception:
        return ""


def resolve_viewer_timezone() -> str:
    """
    Determine the timezone to display times in for the current user:
      1. Use whatever is saved in the DB for this account, if set.
      2. Otherwise try to auto-detect via the browser (best effort).
      3. Otherwise fall back to UTC until the user picks one manually.
    The sidebar always lets the user override/confirm this explicitly, which
    is the reliable path (JS auto-detection can silently fail on some setups).
    """
    username = st.session_state.username
    saved_tz = get_user_timezone(username)
    if saved_tz:
        return saved_tz

    detected = streamlit_js_eval(
        js_expressions="Intl.DateTimeFormat().resolvedOptions().timeZone",
        key="get_tz",
    )
    if detected:
        set_user_timezone(username, detected)
        return detected

    return "UTC"


def login_page():
    st.title("💬 ChatApp")
    st.caption("A WhatsApp-style chat app for multiple users")

    tab_login, tab_signup = st.tabs(["Log In", "Sign Up"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log In", use_container_width=True)
            if submitted:
                username = username.strip()
                if not username or not password:
                    st.error("Please enter both username and password.")
                elif authenticate_user(username, password):
                    st.session_state.logged_in = True
                    st.session_state.username = username
                    st.session_state.active_chat = None
                    st.session_state.page = "dashboard" if is_owner(username) else "chat"
                    st.rerun()
                else:
                    st.error("Invalid username or password.")

    with tab_signup:
        with st.form("signup_form"):
            new_user = st.text_input("Choose a username")
            new_pass = st.text_input("Choose a password", type="password")
            confirm_pass = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create Account", use_container_width=True)
            if submitted:
                new_user = new_user.strip()
                if not new_user or not new_pass:
                    st.error("Username and password cannot be empty.")
                elif new_pass != confirm_pass:
                    st.error("Passwords do not match.")
                elif len(new_pass) < 4:
                    st.error("Password must be at least 4 characters.")
                else:
                    ok, msg = register_user(new_user, new_pass)
                    (st.success if ok else st.error)(msg)


def settings_page():
    st.title("⚙️ Settings")

    st.subheader("Change password")
    with st.form("change_pw_form", clear_on_submit=True):
        current_pw = st.text_input("Current password", type="password")
        new_pw = st.text_input("New password", type="password")
        confirm_pw = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update Password")
        if submitted:
            if not authenticate_user(st.session_state.username, current_pw):
                st.error("Current password is incorrect.")
            elif len(new_pw) < 4:
                st.error("New password must be at least 4 characters.")
            elif new_pw != confirm_pw:
                st.error("New passwords do not match.")
            else:
                change_password(st.session_state.username, new_pw)
                st.success("Password updated successfully.")

    st.divider()
    st.subheader("Time zone")
    current_tz = get_user_timezone(st.session_state.username) or "UTC"
    options = COMMON_TIMEZONES if current_tz in COMMON_TIMEZONES else [current_tz] + COMMON_TIMEZONES
    chosen = st.selectbox(
        "Times in your chats are shown in this timezone",
        options=options,
        index=options.index(current_tz) if current_tz in options else 0,
    )
    with st.expander("Can't find your timezone above? Search the full list"):
        full_choice = st.selectbox("All timezones", options=ALL_TIMEZONES,
                                    index=ALL_TIMEZONES.index(current_tz) if current_tz in ALL_TIMEZONES else 0)
        if st.button("Use this timezone instead"):
            set_user_timezone(st.session_state.username, full_choice)
            st.success(f"Timezone set to {full_choice}.")
            st.rerun()

    if st.button("Save timezone"):
        set_user_timezone(st.session_state.username, chosen)
        st.success(f"Timezone set to {chosen}.")
        st.rerun()


def chat_page():
    # Poll every 3 seconds so new messages show up without a manual refresh
    st_autorefresh(interval=3000, key="autorefresh")
    viewer_tz = resolve_viewer_timezone()

    active_chat = st.session_state.get("active_chat")

    if not active_chat:
        st.title("💬 ChatApp")
        st.info("Select a contact from the sidebar to start chatting.")
        return

    st.subheader(f"Chat with {active_chat}")
    st.caption(f"Times shown in your timezone: {viewer_tz}  •  change this in Settings")
    messages = get_messages(st.session_state.username, active_chat)

    with st.container(height=430):
        if not messages:
            st.caption("No messages yet. Say hi! 👋")
        for msg_id, sender, receiver, content, media_data, media_name, media_type, ts in messages:
            is_me = sender == st.session_state.username
            left, mid, right = st.columns([1, 3, 1])
            bubble_col = right if is_me else left
            with bubble_col:
                bubble_color = "#DCF8C6" if is_me else "#FFFFFF"
                if content:
                    st.markdown(
                        f"""<div style="background-color:{bubble_color}; padding:8px 12px;
                        border-radius:10px; margin-bottom:4px; border:1px solid #ddd;
                        word-wrap:break-word;">
                        <span style="color:#000000; font-size:15px;">{content}</span>
                        <div style="font-size:10px; color:#555555; text-align:right; margin-top:2px;">
                        {format_time(ts, viewer_tz)}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
                if media_data:
                    # Media is stored and served at full original quality - no compression is applied.
                    if media_type and media_type.startswith("image"):
                        st.image(media_data, use_container_width=True)
                    elif media_type and media_type.startswith("video"):
                        st.video(media_data)
                    elif media_type and media_type.startswith("audio"):
                        st.audio(media_data)

                    st.download_button(
                        label=f"⬇️ Download {media_name}",
                        data=media_data,
                        file_name=media_name,
                        key=f"dl_{msg_id}",
                        use_container_width=True,
                    )
                    if not content:
                        st.caption(format_time(ts, viewer_tz))

                if is_me:
                    if st.button("🗑️ Delete", key=f"del_{msg_id}", use_container_width=True):
                        delete_message(msg_id, st.session_state.username)
                        st.rerun()

    st.divider()
    with st.form("message_form", clear_on_submit=True):
        col1, col2 = st.columns([4, 1])
        with col1:
            text = st.text_input(
                "Message", label_visibility="collapsed", placeholder="Type a message..."
            )
        with col2:
            uploaded_file = st.file_uploader(
                "Attach", label_visibility="collapsed", key="uploader"
            )
        send = st.form_submit_button("Send ➤", use_container_width=True)

        if send:
            if not text and not uploaded_file:
                st.warning("Type a message or attach a file first.")
            else:
                media_data = media_name = media_type = None
                if uploaded_file is not None:
                    media_data = uploaded_file.getvalue()
                    media_name = uploaded_file.name
                    media_type = uploaded_file.type
                send_message(
                    st.session_state.username,
                    active_chat,
                    content=text if text else None,
                    media_data=media_data,
                    media_name=media_name,
                    media_type=media_type,
                )
                st.rerun()


def sidebar_nav():
    with st.sidebar:
        st.markdown(f"### 👤 {st.session_state.username}")
        if is_owner(st.session_state.username):
            st.caption("🛠️ App Owner")
        if st.button("Log Out", use_container_width=True):
            for key in ("logged_in", "username", "active_chat", "page", "timezone"):
                st.session_state.pop(key, None)
            st.rerun()

        st.divider()
        nav_options = ["💬 Chats", "⚙️ Settings"]
        if is_owner(st.session_state.username):
            nav_options.insert(1, "📊 Dashboard")
        current_label = {
            "chat": "💬 Chats",
            "dashboard": "📊 Dashboard",
            "settings": "⚙️ Settings",
        }.get(st.session_state.get("page", "chat"), "💬 Chats")
        chosen = st.radio("Navigate", nav_options,
                           index=nav_options.index(current_label) if current_label in nav_options else 0,
                           label_visibility="collapsed")
        st.session_state.page = {
            "💬 Chats": "chat",
            "📊 Dashboard": "dashboard",
            "⚙️ Settings": "settings",
        }[chosen]

        if st.session_state.page == "chat":
            st.divider()
            st.markdown("#### Contacts")
            users = get_all_users(exclude=st.session_state.username)
            if not users:
                st.info("No other users yet. Ask someone to sign up!")
            for u in users:
                is_active = st.session_state.get("active_chat") == u
                if st.button(f"{'🟢' if is_active else '💬'} {u}", key=f"contact_{u}", use_container_width=True):
                    st.session_state.active_chat = u
                    st.rerun()


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
init_db()

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "active_chat" not in st.session_state:
    st.session_state.active_chat = None
if "page" not in st.session_state:
    st.session_state.page = "chat"

if st.session_state.logged_in:
    sidebar_nav()
    if st.session_state.page == "dashboard" and is_owner(st.session_state.username):
        dashboard_page()
    elif st.session_state.page == "settings":
        settings_page()
    else:
        chat_page()
else:
    login_page()
