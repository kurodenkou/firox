# Firox Linux: neofetch is no longer packaged for Fedora, and screenfetch can
# only show Fedora's logo, so both names run fastfetch, which shows Rune, the
# Firox mascot (/etc/xdg/fastfetch/config.jsonc). If you install either one yourself,
# the real program wins.
case $- in *i*) ;; *) return 0 2>/dev/null ;; esac
# Rune is a picture drawn in 24-bit colour: on the text console, which
# has 16 colours, the text logo instead
if [ "$TERM" = linux ]; then
    alias fastfetch='fastfetch --logo-type file --logo /usr/share/firox/fastfetch/logo.txt'
fi
command -v neofetch >/dev/null 2>&1 || alias neofetch='fastfetch'
command -v screenfetch >/dev/null 2>&1 || alias screenfetch='fastfetch'
