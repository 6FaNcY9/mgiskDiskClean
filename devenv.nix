{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:
{
  languages.python = {
    enable = true;
    venv = {
      enable = true;
      requirements = ''
        pytest>=8.0
        reportlab>=4.0
      '';
    };
  };

  packages = with pkgs; [
    jq
    curl
    rsync
  ];
  scripts = {
    scan-mailbox.exec = ''
      if [ -z "$1" ]; then
        echo "Usage: scan-mailbox <mailbox>"
        echo "  e.g. scan-mailbox gabriel.hangel"
        exit 1
      fi
      MAILBOX="$1"
      LOCAL_MAIL=/tmp/mrija_maildir/$MAILBOX
      mkdir -p $LOCAL_MAIL

      echo "==> Pulling maildir from server (may take a while)..."
      rsync -az --info=progress2 \
        mrija_org@s16.thehost.com.ua:email/mrija.org/$MAILBOX/.maildir/ \
        $LOCAL_MAIL/.maildir/ \
        || { echo "ERROR: rsync failed"; exit 1; }

      echo "==> Generating report..."
      python -m maildir_report \
        $LOCAL_MAIL/.maildir \
        $DEVENV_ROOT/reports \
        || { echo "ERROR: report generation failed"; exit 1; }

      echo "==> Done. Outputs written to $DEVENV_ROOT/reports/"
    '';
  };
  enterShell = ''
    echo ""
    echo "  maildir-pdf-report devenv"
    echo "  ──────────────────────────────────────────────────────"
    echo "  scan-mailbox <mailbox>   rsync maildir locally, generate PDF/manifest/decisions"
    echo "  ──────────────────────────────────────────────────────"
    echo "  reports : $DEVENV_ROOT/reports/"
    echo "  logs    : $DEVENV_ROOT/logs/"
    echo ""
  '';
}
