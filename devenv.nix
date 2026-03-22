{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:
{
  packages = with pkgs; [
    nginx
    jq
    curl
    rsync
    apacheHttpd
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

      echo "==> Running scanner locally..."
      python $DEVENV_ROOT/scripts/maildir_viewer.py $LOCAL_MAIL/.maildir \
        || { echo "ERROR: scanner failed"; exit 1; }

      echo "==> Deploying report..."
      cp $LOCAL_MAIL/mail_viewer.html $DEVENV_ROOT/reports/index.html \
        || { echo "ERROR: report not found — did the scanner finish?"; exit 1; }
      cp $LOCAL_MAIL/mail_viewer.json $DEVENV_ROOT/reports/mail_viewer.json 2>/dev/null || true

      echo "==> Done. Run serve-reload"
    '';
    scan-upload.exec = ''
      echo "Uploading scripts to server..."
      scp $DEVENV_ROOT/scripts/maildir_scan.py mrija_org@s16.thehost.com.ua:~/
      scp $DEVENV_ROOT/scripts/disk_scan.py mrija_org@s16.thehost.com.ua:~/
      echo "Done. SSH in and run:"
      echo "  python ~/maildir_scan.py email/mrija.org/<mailbox>/.maildir"
      echo "  python ~/disk_scan.py /var/www/mrija_org/data"
    '';
    pull-report.exec = ''
      if [ -z "$1" ]; then
        echo "Usage: pull-report email/mrija.org/<mailbox>/.maildir"
        exit 1
      fi
      REMOTE="/var/www/mrija_org/data/$1/../mail_report.html"
      scp mrija_org@s16.thehost.com.ua:"$REMOTE" $DEVENV_ROOT/reports/index.html
      echo "Deployed to reports/index.html — run serve-reload"
    '';
    pull-disk-report.exec = ''
      scp mrija_org@s16.thehost.com.ua:~/disk_report.html $DEVENV_ROOT/reports/index.html \
        || { echo "ERROR: could not pull disk_report.html"; exit 1; }
      echo "Deployed disk report — run serve-reload"
    '';
    deploy-report.exec = ''
      if [ -z "$1" ]; then
        echo "Usage: deploy-report /path/to/report.html"
        exit 1
      fi
      cp "$1" $DEVENV_ROOT/reports/index.html \
        || { echo "ERROR: could not copy $1"; exit 1; }
      echo "Deployed. Run serve-reload."
    '';
    set-password.exec = ''
      htpasswd -c $DEVENV_ROOT/nginx/.htpasswd boss
      echo "Done. Run serve-reload."
    '';
    serve-start.exec = "nginx -c $DEVENV_ROOT/nginx/nginx.conf -p $DEVENV_ROOT -e $DEVENV_ROOT/logs/error.log";
    serve-stop.exec = "nginx -c $DEVENV_ROOT/nginx/nginx.conf -p $DEVENV_ROOT -e $DEVENV_ROOT/logs/error.log -s stop";
    serve-reload.exec = "nginx -c $DEVENV_ROOT/nginx/nginx.conf -p $DEVENV_ROOT -e $DEVENV_ROOT/logs/error.log -s reload";
  };
  enterShell = ''
    echo ""
    echo "  mgiskDiskClean devenv"
    echo "  ──────────────────────────────────────────────────────"
    echo "  scan-mailbox <mailbox>   rsync maildir locally, scan, deploy"
    echo "  scan-upload              upload legacy scripts to server"
    echo "  pull-report <maildir>    pull mail_report.html from server"
    echo "  pull-disk-report         pull disk_report.html from server"
    echo "  deploy-report <file>     deploy local html to reports/"
    echo "  set-password             set/reset boss nginx login password"
    echo "  serve-start              start nginx on :8765"
    echo "  serve-stop               stop nginx"
    echo "  serve-reload             reload nginx (no downtime)"
    echo "  ──────────────────────────────────────────────────────"
    echo "  reports : $DEVENV_ROOT/reports/"
    echo "  logs    : $DEVENV_ROOT/logs/"
    echo ""
  '';
}
