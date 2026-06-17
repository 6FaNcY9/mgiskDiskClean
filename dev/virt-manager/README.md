# virt-manager Windows Share

Use this when the full repository is too large to ZIP into the Windows VM.

The recommended setup is:

1. Linux host keeps the real git checkout.
2. Linux host exports a small sibling directory, excluding `.git`, `data`, logs,
   reports, and generated archives.
3. virt-manager mounts that sibling directory into the Windows 10 Pro VM as a
   read-only filesystem/share.
4. Inside Windows, run `dev\windows\copy-from-readonly-share.ps1` from the share
   to copy the source into `C:\Dev\mrijaPageClean`.
5. Run the Windows dev scripts from the writable copy.

## Refresh Share From Linux Host

From the repo root:

```bash
bash dev/virt-manager/refresh-windows-share.sh
```

Default output:

```text
/home/vino/Documents/Projekts/mrijaWindowsClientShare
```

## Why Copy Inside Windows?

The app and dev scripts need to write generated files:

- `data/client/mail_archive.sqlite`
- Python test caches

A read-only VM share is good for secure transfer, but not for running the app
directly.
