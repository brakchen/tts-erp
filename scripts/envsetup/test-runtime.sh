#!/usr/bin/env bash
# Source in test commands. One optional, stable Node installation per host.
if [[ -n "${TTS_ERP_TEST_NODE_BIN:-}" ]]; then
  # PATH discovery itself must not make browser-free selections require Node.
  # The frontend preparation/build commands validate the actual engine.
  export PATH="$TTS_ERP_TEST_NODE_BIN:$PATH"
elif [[ -x "${HOME}/.local/tts-erp-test-node/node_modules/.bin/node" ]]; then
  export PATH="${HOME}/.local/tts-erp-test-node/node_modules/.bin:$PATH"
fi
