"""Encrypted Wi-Fi: the host's own certificate, made once and kept in tls/
next to this file. The iPad app trusts it by its pairing code (the start of
the certificate's SHA-256 fingerprint), which the host prints in its window:
the first time the app connects it shows the code and asks you to check it
matches, then remembers it, so nobody else on the Wi-Fi can read the stream,
the keys you type or the clipboard, or pretend to be this PC.

Needs the optional 'cryptography' package (start.bat installs it). Without it
the host simply offers the plain address as before."""
import datetime
import hashlib
import os
import socket
import ssl

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "tls")
CERT, KEY = os.path.join(DIR, "cert.pem"), os.path.join(DIR, "key.pem")


def _make():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"iPad Display on {socket.gethostname()}"[:64])])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    os.makedirs(DIR, exist_ok=True)
    with open(KEY, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    with open(CERT, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def fingerprint():
    with open(CERT, encoding="ascii") as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    return hashlib.sha256(der).hexdigest().upper()


def pair_code(fp):
    return f"{fp[:4]}-{fp[4:8]}"


def context():
    """(ssl context, pairing code), or (None, reason) when it can't be set up."""
    try:
        if not (os.path.exists(CERT) and os.path.exists(KEY)):
            _make()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(CERT, KEY)
        return ctx, pair_code(fingerprint())
    except ImportError:
        return None, "the 'cryptography' package isn't installed (run start.bat once with internet)"
    except Exception as e:
        return None, str(e)
