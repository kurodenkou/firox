# Firox Linux: neofetch is no longer packaged for Fedora, and screenfetch can
# only show Fedora's logo, so both names run fastfetch, which shows the Firox
# logo (/etc/xdg/fastfetch/config.jsonc). If you install either one yourself,
# the real program wins.
case $- in *i*) ;; *) return 0 2>/dev/null ;; esac
command -v neofetch >/dev/null 2>&1 || alias neofetch='fastfetch'
command -v screenfetch >/dev/null 2>&1 || alias screenfetch='fastfetch'
