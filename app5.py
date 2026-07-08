import streamlit as st
import sqlite3
import hashlib
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
st.set_page_config(page_title="ChatApp", page_icon="💬", layout="wide")
DB_PATH = "chatapp.db"
SALT = "chatapp_static_salt_v1"  # not a substitute for per-user salts in production


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
            created_at TEXT
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
    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    return hashlib.sha256((SALT + password).encode()).hexdigest()


def register_user(username: str, password: str):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, hash_password(password), datetime.now().isoformat()),
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


def send_message(sender, receiver, content=None, media_data=None, media_name=None, media_type=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO messages (sender, receiver, content, media_data, media_name, media_type, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (sender, receiver, content, media_data, media_name, media_type, datetime.now().isoformat()),
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


init_db()


# ---------------------------------------------------------------------------
# UI HELPERS
# ---------------------------------------------------------------------------
def format_time(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%I:%M %p")
    except Exception:
        return ""


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


def chat_page():
    # Poll every 3 seconds so new messages show up without a manual refresh
    st_autorefresh(interval=3000, key="autorefresh")

    with st.sidebar:
        st.markdown(f"### 👤 {st.session_state.username}")
        if st.button("Log Out", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.username = None
            st.session_state.active_chat = None
            st.rerun()

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

    active_chat = st.session_state.get("active_chat")

    if not active_chat:
        st.title("💬 ChatApp")
        st.info("Select a contact from the sidebar to start chatting.")
        return

    st.subheader(f"Chat with {active_chat}")
    messages = get_messages(st.session_state.username, active_chat)

    with st.container(height=450):
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
                        {content}
                        <div style="font-size:10px; color:gray; text-align:right; margin-top:2px;">
                        {format_time(ts)}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
                if media_data:
                    if media_type and media_type.startswith("image"):
                        st.image(media_data, width=250)
                    elif media_type and media_type.startswith("video"):
                        st.video(media_data)
                    elif media_type and media_type.startswith("audio"):
                        st.audio(media_data)
                    else:
                        st.download_button(
                            label=f"📎 {media_name}",
                            data=media_data,
                            file_name=media_name,
                            key=f"dl_{msg_id}",
                        )
                    if not content:
                        st.caption(format_time(ts))

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


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "active_chat" not in st.session_state:
    st.session_state.active_chat = None

if st.session_state.logged_in:
    chat_page()
else:
    login_page()
