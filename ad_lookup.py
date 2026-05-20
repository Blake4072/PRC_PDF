import os
from ldap3 import Server, Connection, ALL, NTLM

AD_SERVER = os.environ.get("PRC_AD_SERVER")
AD_BASE_DN = os.environ.get("PRC_AD_BASE_DN")
AD_BIND_USER = os.environ.get("PRC_AD_BIND_USER")
AD_BIND_PASS = os.environ.get("PRC_AD_BIND_PASS")

def fetch_ad_recipient_emails():
    """
    Returns [] if AD is not configured yet.
    """
    if not all([AD_SERVER, AD_BASE_DN, AD_BIND_USER, AD_BIND_PASS]):
        return []

    server = Server(AD_SERVER, get_info=ALL)
    conn = Connection(
        server,
        user=AD_BIND_USER,
        password=AD_BIND_PASS,
        authentication=NTLM,
        auto_bind=True,
    )

    conn.search(
        AD_BASE_DN,
        "(mail=*)",
        attributes=["mail"],
    )

    return sorted(
        e.mail.value
        for e in conn.entries
        if e.mail and e.mail.value.endswith("@blessinghealth.org")
    )