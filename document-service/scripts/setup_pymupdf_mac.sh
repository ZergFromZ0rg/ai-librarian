#!/usr/bin/env bash
# Helper script to prepare macOS for building PyMuPDF from source.
# Usage: bash document-service/scripts/setup_pymupdf_mac.sh

set -euo pipefail

echo "== Preparing environment for PyMuPDF build =="

echo "1) Ensure Xcode Command Line Tools are installed"
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Installing Xcode Command Line Tools..."
  xcode-select --install || true
else
  echo "Xcode CLT already installed"
fi

echo "2) Install Homebrew packages (openssl@3, pkg-config)"
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found — please install Homebrew first: https://brew.sh/"
  exit 1
fi

echo "Installing openssl@3 and pkg-config via brew"
brew install openssl@3 pkg-config || true

# Determine Homebrew prefix for Apple Silicon vs Intel
BREW_PREFIX=$(brew --prefix)
OPENSSL_PREFIX="${BREW_PREFIX}/opt/openssl@3"

echo "3) Export build flags so the PyMuPDF build can find OpenSSL headers/libs"
export LDFLAGS="-L${OPENSSL_PREFIX}/lib"
export CPPFLAGS="-I${OPENSSL_PREFIX}/include"
export PKG_CONFIG_PATH="${OPENSSL_PREFIX}/lib/pkgconfig"

if command -v xcrun >/dev/null 2>&1; then
  export SDKROOT=$(xcrun --show-sdk-path)
fi

echo "LDFLAGS=$LDFLAGS"
echo "CPPFLAGS=$CPPFLAGS"
echo "PKG_CONFIG_PATH=$PKG_CONFIG_PATH"

echo "4) Upgrade pip/setuptools/wheel and attempt to install PyMuPDF"
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install --no-cache-dir --force-reinstall PyMuPDF==1.22.5

echo "If the install succeeds, you're good. If it still fails, consider installing Python 3.11 and using its venv as an alternative (prebuilt wheels are available for 3.11)."

echo "Done."
