#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
sh tools/activation/test.sh
swift test --package-path Packages/FirmwareKit --filter ActivationTests
