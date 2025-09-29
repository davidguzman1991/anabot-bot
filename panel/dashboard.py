import os
from datetime import datetime

import pandas as pd
import psycopg2
import streamlit as st


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def conn():
    return psycopg2.connect(
        dbname=_env("PGDATABASE", "postgres"),
        user=_env("PGUSER", "postgres"),
        password=_env("PGPASSWORD", ""),
        host=_env("PGHOST", "localhost"),
        port=_env("PGPORT", "5432"),
    )


def query(sql: str, params=None) -> pd.DataFrame:
    with conn() as c:
        return pd.read_sql(sql, c, params=params)


def exec_sql(sql: str, params=None) -> None:
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            c.commit()


st.set_page_config(page_title="AnaBot cPanel", layout="wide")
st.sidebar.title("📊 AnaBot cPanel")
menu = st.sidebar.radio(
    "Menú",
    ["Conversaciones", "Detalle", "Citas", "Métricas", "Gestión"],
)

try:
    if menu == "Conversaciones":
        st.header("📋 Conversaciones")
        term = st.text_input("Buscar por user_id (parcial):")
        df = query(
            """
            SELECT user_id,
                   MAX(created_at) AS last_time,
                   MAX(COALESCE(NULLIF(message,''), NULLIF(response,''))) AS last_text,
                   BOOL_OR(handoff) AS handoff,
                   MAX(status) AS status
            FROM conversation_logs
            WHERE (%s IS NULL OR user_id ILIKE '%%' || %s || '%%')
            GROUP BY user_id
            ORDER BY last_time DESC
            LIMIT 500
            """,
            (term if term else None, term if term else None),
        )
        st.dataframe(df, use_container_width=True)

    elif menu == "Detalle":
        st.header("💬 Detalle de conversación")
        uid = st.text_input("user_id del paciente:")
        if uid:
            rows = query(
                """
                SELECT created_at,
                       CASE WHEN message IS NOT NULL AND message<>'' THEN 'paciente' ELSE 'bot' END AS actor,
                       COALESCE(NULLIF(message,''), response) AS texto,
                       handoff,
                       status
                  FROM conversation_logs
                 WHERE user_id=%s
                 ORDER BY created_at ASC
                """,
                (uid,),
            )
            if rows.empty:
                st.info("Sin registros para ese usuario.")
            else:
                for _, r in rows.iterrows():
                    label = "🧑 Paciente" if r["actor"] == "paciente" else "🤖 Bot"
                    st.markdown(f"**{r['created_at']}** — {label}: {r['texto']}")
                    if r["handoff"]:
                        st.warning("⚠️ Conversación derivada a humano")
                    st.caption(f"Estado: {r['status']}")
                    st.divider()

    elif menu == "Citas":
        st.header("📅 Citas")
        estado = st.selectbox(
            "Filtrar por estado:", ["todos", "pendiente", "confirmada", "cancelada"], index=0
        )
        base = "SELECT id, user_id, appointment_date, status, created_at FROM appointments"
        where = "" if estado == "todos" else " WHERE status=%s"
        citas = query(base + where + " ORDER BY appointment_date DESC", (estado,) if estado != "todos" else None)
        st.dataframe(citas, use_container_width=True)

        st.subheader("Actualizar estado de cita")
        cid = st.number_input("ID de cita", min_value=1, step=1, format="%d")
        new_status = st.selectbox("Nuevo estado", ["pendiente", "confirmada", "cancelada"], index=0)
        if st.button("Actualizar cita"):
            exec_sql("UPDATE appointments SET status=%s WHERE id=%s", (new_status, int(cid)))
            st.success("Cita actualizada")

    elif menu == "Métricas":
        st.header("📈 Métricas")
        conversations_today = query(
            "SELECT COUNT(DISTINCT user_id) AS total FROM conversation_logs WHERE created_at::date = NOW()::date"
        )
        handoffs_today = query(
            "SELECT COUNT(*) AS total FROM conversation_logs WHERE handoff = TRUE AND created_at::date = NOW()::date"
        )
        future_appts = query(
            "SELECT COUNT(*) AS total FROM appointments WHERE appointment_date::date >= NOW()::date"
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Conversaciones hoy", int(conversations_today.iloc[0, 0]) if not conversations_today.empty else 0)
        c2.metric("Handoff hoy", int(handoffs_today.iloc[0, 0]) if not handoffs_today.empty else 0)
        c3.metric("Citas futuras", int(future_appts.iloc[0, 0]) if not future_appts.empty else 0)

    elif menu == "Gestión":
        st.header("⚙️ Gestión de conversaciones")
        uid = st.text_input("user_id a actualizar:")
        new_status = st.selectbox("Nuevo estado", ["pendiente", "atendida", "cerrada"], index=1)
        if st.button("Actualizar conversación"):
            exec_sql(
                "UPDATE conversation_logs SET status=%s WHERE user_id=%s",
                (new_status, uid),
            )
            st.success(f"Estado de {uid} actualizado a {new_status}")

except Exception as exc:
    st.error(f"Error consultando la base de datos: {exc}")
    st.stop()
