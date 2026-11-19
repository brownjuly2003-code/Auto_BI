#!/bin/bash
# Run an E4a stand script with the PATH docker and colima need; output to logs/<name>.log + .exit.
# Usage: bash ~/e4astand/m.sh NAME CMD...
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "${E4A_STAND:-$HOME/e4astand}" || exit 9
name="$1"; shift
rm -f "logs/$name.exit"
"$@" > "logs/$name.log" 2>&1
echo $? > "logs/$name.exit"
