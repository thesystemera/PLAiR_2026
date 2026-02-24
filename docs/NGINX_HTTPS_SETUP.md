# NGINX & HTTPS Certificate Setup

## Overview

PLAiR.live uses nginx on Windows with Let's Encrypt SSL certificates managed through certbot in WSL (Ubuntu).

## Infrastructure

- **Nginx:** `C:\nginx\` (Windows native)
- **Certbot:** WSL Ubuntu (requires WSL to be running)
- **Certificates:** `C:\Certbot\live\plair.live\`
- **Webroot:** `C:\nginx\html` (for ACME challenges)

## Current Certificate

- **Domains:** plair.live, www.plair.live
- **Renewed:** January 3, 2026
- **Expires:** April 3, 2026 (3 months validity)
- **Authenticator:** webroot (automatic renewal capable)

## Auto-Renewal Setup

**Requirements:**
- WSL Ubuntu must be running for auto-renewal to work
- Certbot systemd timer runs twice daily (checks for renewal when cert is <30 days from expiry)

**Timer Status:**
```bash
wsl bash -c "echo 'amiga4eva' | sudo -S systemctl status certbot.timer"
```

**Post-Renewal Hook:**
Located at `/etc/letsencrypt/renewal-hooks/deploy/copy-to-windows.sh` in WSL:
- Copies renewed certificates to `C:\Certbot\live\plair.live\`
- Reloads nginx automatically

## Manual Renewal (if needed)

### Step 1: Renew Certificate
```bash
wsl bash -c "echo 'amiga4eva' | sudo -S certbot renew --force-renewal --no-random-sleep-on-renew"
```

### Step 2: Copy Certificates to Windows
```bash
wsl bash -c "echo 'amiga4eva' | sudo -S bash -c 'cp /etc/letsencrypt/live/plair.live/fullchain.pem /mnt/c/Certbot/live/plair.live/ && cp /etc/letsencrypt/live/plair.live/privkey.pem /mnt/c/Certbot/live/plair.live/'"
```

### Step 3: Reload Nginx
```bash
cd C:/nginx && ./nginx.exe -s reload
```

### Step 4: Verify HTTPS
```bash
curl -I https://plair.live
```

## Check Certificate Status

**View current certificate:**
```bash
wsl bash -c "echo 'amiga4eva' | sudo -S certbot certificates"
```

**Check expiry date:**
```bash
wsl bash -c "echo 'amiga4eva' | sudo -S openssl x509 -in /etc/letsencrypt/live/plair.live/fullchain.pem -noout -dates"
```

## Test Auto-Renewal

**Dry run (doesn't actually renew):**
```bash
wsl bash -c "echo 'amiga4eva' | sudo -S certbot renew --dry-run --no-random-sleep-on-renew"
```

## Nginx Configuration

SSL certificates configured in `C:\nginx\conf\nginx.conf`:
```nginx
server {
    listen 443 ssl;
    server_name plair.live www.plair.live;

    ssl_certificate "C:/Certbot/live/plair.live/fullchain.pem";
    ssl_certificate_key "C:/Certbot/live/plair.live/privkey.pem";

    # ACME challenge location for renewals
    location /.well-known/acme-challenge/ {
        root C:/nginx/html;
    }
}
```

## Troubleshooting

### Certificate Expired
If you see "INVALID: EXPIRED" when checking certificates, follow the manual renewal steps above.

### Auto-Renewal Not Working
- Check if WSL is running: `wsl --list --verbose`
- Check timer status: `wsl bash -c "echo 'amiga4eva' | sudo -S systemctl status certbot.timer"`
- Check renewal config: `wsl bash -c "echo 'amiga4eva' | sudo -S cat /etc/letsencrypt/renewal/plair.live.conf"`
- Should show `authenticator = webroot` (NOT `manual`)

### Nginx Won't Start After Renewal
- Check certificate files exist: `dir C:\Certbot\live\plair.live`
- Test nginx config: `cd C:\nginx && nginx.exe -t`
- Check nginx error log: `type C:\nginx\logs\error.log`

### "Manual Plugin" Error
This means the certificate was created with manual DNS challenge. Re-create with webroot:
```bash
wsl bash -c "echo 'amiga4eva' | sudo -S certbot certonly --webroot -w /mnt/c/nginx/html -d plair.live -d www.plair.live --force-renewal"
```

## Important Notes

- Certificates renew automatically when within 30 days of expiry
- Timer checks twice daily but only renews when needed
- WSL must remain running for auto-renewal to work
- Manual renewal is safe to run anytime (won't affect existing cert unless you use `--force-renewal`)

## Quick Reference Commands

```bash
# Check cert status
wsl bash -c "echo 'amiga4eva' | sudo -S certbot certificates"

# Manual renew + copy + reload (all-in-one)
wsl bash -c "echo 'amiga4eva' | sudo -S certbot renew --force-renewal --no-random-sleep-on-renew && sudo cp /etc/letsencrypt/live/plair.live/fullchain.pem /mnt/c/Certbot/live/plair.live/ && sudo cp /etc/letsencrypt/live/plair.live/privkey.pem /mnt/c/Certbot/live/plair.live/" && cd C:/nginx && ./nginx.exe -s reload

# Test HTTPS
curl -I https://plair.live

# Check timer
wsl bash -c "echo 'amiga4eva' | sudo -S systemctl list-timers | grep certbot"
```
