#!/usr/bin/env bash
# Create a local, self-signed code-signing identity for WhisperN. Run once.
#
# WHY. build_app.sh used to sign ad hoc. An ad-hoc signature's designated
# requirement is the build's cdhash, and macOS privacy grants (Microphone,
# Automation, Documents) attach to that requirement -- so every
# rebuild looked like a new application and the grants silently stopped
# applying. Signed with a fixed certificate, the requirement becomes
# `identifier "local.whispern" and certificate leaf = H"<cert hash>"`, which
# survives rebuilds.
#
# WHAT IT DOES. Generates an RSA key and a 10-year certificate restricted to
# code signing, imports both into the login keychain, and lets only
# /usr/bin/codesign use the key. Nothing leaves this Mac and no Apple account
# is involved. The certificate is not trusted system-wide and is not needed
# to be: TCC matches the certificate hash, not a trust chain.
#
# The key and certificate exist on disk only inside a private temporary
# directory, deleted on exit.
#
# Undo:  security delete-identity -c "WhisperN Local Signing"
#
# Usage: scripts/setup_signing.sh

set -euo pipefail

NAME="WhisperN Local Signing"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if security find-certificate -c "$NAME" "$KEYCHAIN" >/dev/null 2>&1; then
    echo "identity already present: $NAME"
    exit 0
fi

command -v openssl >/dev/null || { echo "error: openssl not found" >&2; exit 1; }

umask 077
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/cert.cnf" <<CNF
[req]
distinguished_name = dn
prompt = no
x509_extensions = ext
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
subjectKeyIdentifier = hash
CNF

openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 -nodes \
    -config "$WORK/cert.cnf" -keyout "$WORK/key.pem" -out "$WORK/cert.pem" 2>/dev/null

# The PKCS#12 password only protects the file for the few milliseconds it
# exists; it is random and never stored. -legacy because macOS `security`
# cannot read OpenSSL 3's default PKCS#12 encryption.
PASS="$(openssl rand -hex 24)"
openssl pkcs12 -export -legacy -inkey "$WORK/key.pem" -in "$WORK/cert.pem" \
    -name "$NAME" -passout "pass:$PASS" -out "$WORK/identity.p12"

security import "$WORK/identity.p12" -k "$KEYCHAIN" -P "$PASS" \
    -T /usr/bin/codesign >/dev/null

echo "created: $NAME (login keychain, usable by codesign only)"
echo "rebuild with: scripts/build_app.sh --install"
