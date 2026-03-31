# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |
| 0.x     | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability within Picobot, please report it responsibly.

### How to Report

1. **Do NOT** create a public GitHub issue for security vulnerabilities
2. Email the maintainers directly or use GitHub's private vulnerability reporting
3. Include as much information as possible:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

### What to Expect

- Acknowledgment of your report within 48 hours
- Regular updates on the progress
- Credit for the discovery (unless you prefer anonymity)

## Security Best Practices

When using Picobot:

- **API Keys**: Never commit API keys to version control
- **Environment Variables**: Use `.env` files and ensure they're in `.gitignore`
- **Permissions**: Run with minimal required permissions
- **Updates**: Keep Picobot updated to the latest version

## Security Features

Picobot includes several security features:

- Shell command allowlisting
- Workspace restriction for file operations
- DAX approval workflow for file modifications
- Rate limiting on external API calls
