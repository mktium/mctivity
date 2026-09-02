import ipaddress


def is_loopback_host(host):
    host = str(host).strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_web_access(host, token):
    if is_loopback_host(host):
        return
    if (len(token) < 32 or len(set(token)) < 8 or "<" in token or ">" in token
            or any(ord(char) < 33 or ord(char) > 126 for char in token)):
        raise ValueError(
            "Non-loopback HMI access requires a randomly generated "
            "MCTIVITY_API_TOKEN of at least 32 characters."
        )
