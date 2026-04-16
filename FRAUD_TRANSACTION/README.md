# K-P.Krish-2325

## New Login Flow (Web 1 + Web 2)

### Web 1
- Login page now has 2 role icons:
- `Admin`: login with `email + password`, then OTP verification from email.
- `User`: login with `username + password`.

### Web 2
- Admin-only access is enabled.
- Non-admin users are redirected to `/login`.
- Admin login is `name + password`.

## Default Admin Credentials

### Web 1 Admin
- Email: `admin@fraudwatch.local`
- Password: `Admin@123`

You can override using env vars:
- `WEB1_ADMIN_NAME`
- `WEB1_ADMIN_EMAIL`
- `WEB1_ADMIN_PASSWORD`

### Web 2 Admin
- Name: `admin`
- Password: `Admin@123`

You can override using env vars:
- `WEB2_ADMIN_NAME`
- `WEB2_ADMIN_PASSWORD`

## OTP Email Setup (Web 1 Admin Login)

Set these env vars (for OTP email sending):
- `SMTP_HOST`
- `SMTP_PORT` (default `587`)
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM` (optional)

If SMTP vars are missing, admin OTP send will fail with a clear error message.
