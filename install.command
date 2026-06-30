#!/bin/bash
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "請先安裝 uv: https://docs.astral.sh/uv/"; exit 1; }
chmod +x MAW.command install.command 2>/dev/null || true

# macOS Finder icon setup
if [[ "$OSTYPE" == "darwin"* ]] && command -v clang >/dev/null; then
    mkdir -p .tmp_build
    clang -framework AppKit -framework Foundation -o .tmp_build/seticon -x objective-c - <<'EOF' 2>/dev/null
#import <AppKit/AppKit.h>
int main(int argc, char *argv[]) {
    @autoreleasepool {
        if (argc < 3) return 1;
        NSImage *image = [[NSImage alloc] initWithContentsOfFile:[NSString stringWithUTF8String:argv[1]]];
        if (!image) return 1;
        [[NSWorkspace sharedWorkspace] setIcon:image forFile:[NSString stringWithUTF8String:argv[2]] options:0];
    }
    return 0;
}
EOF
    if [ -f .tmp_build/seticon ]; then
        .tmp_build/seticon static/installer_icon.png install.command 2>/dev/null || true
        .tmp_build/seticon static/main_app_icon.png MAW.command 2>/dev/null || true
        rm -rf .tmp_build
    fi
fi

uv sync
[ -f .env ] || cp .env.example .env
exec ./MAW.command
