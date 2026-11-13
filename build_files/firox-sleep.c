/*
 * firox-sleep.exe: the stand-in program that keeps the shared Windows
 * container running (firox-windows --host). It does nothing but wait,
 * with no window, so Windows programs started later can run inside the
 * same Steam Runtime container and close in any order. firox-windows
 * stops it at logout or with --stop-host.
 *
 * Built by build_files/build.sh with Fedora's MinGW cross-compiler:
 *   x86_64-w64-mingw32-gcc -O2 -s -mwindows -o firox-sleep.exe firox-sleep.c
 */
#include <windows.h>

int WINAPI WinMain(HINSTANCE instance, HINSTANCE previous, LPSTR command, int show)
{
    (void)instance; (void)previous; (void)command; (void)show;
    for (;;)
        Sleep(INFINITE);
    return 0;
}
