# Copilot Instructions for iregul-proxy

## Repository Overview

**iregul-proxy** is a local proxy server for heat pumps based on the iregul system. The repository is currently in its early development stage.

**License:** GNU General Public License v3.0

## Project Information

### Current State
- This repository is in early development and does not yet have source code implemented
- The project will be a proxy server for interfacing with heat pump systems using the iregul protocol
- Technology stack has not been determined yet

### Expected Development
When code is added to this repository, it will likely include:
- A proxy server implementation (possibly in Node.js, Python, Go, or similar)
- Configuration files for the heat pump system integration
- API endpoints or communication protocols for the iregul system
- Documentation for setup and usage

## Build and Development Instructions

### Initial Setup
Since the project is in early stages, build instructions will be added as the codebase develops. When adding build instructions:
- Always document the exact versions of tools and runtimes required
- Include step-by-step setup instructions from a clean environment
- Document any environment variables or configuration needed
- Test all commands in a clean environment before documenting

### Testing
- Test infrastructure should be added as code is developed
- Always run tests before committing changes
- Document the test command and expected output

### Linting and Code Quality
- Add linting configuration appropriate to the chosen technology stack
- Always run linters before committing
- Follow the code style conventions established in the codebase

## Important Guidelines

### For Code Changes
- Keep changes minimal and focused on the specific issue being addressed
- Avoid making unrelated changes or "improvements" outside the scope of the task
- Ensure all changes are compatible with the GPL v3.0 license
- Test changes thoroughly before committing

### For New Features
- Ensure new code follows best practices for the chosen language/framework
- Add appropriate error handling and logging
- Document any new APIs or interfaces
- Consider security implications, especially for proxy functionality

### For Dependencies
- Always check for security vulnerabilities before adding new dependencies
- Prefer well-maintained, popular libraries with active communities
- Document why each dependency is needed
- Keep dependencies up to date

## Project Structure

Currently, the repository contains:
- `README.md` - Project description
- `LICENSE` - GPL v3.0 license file
- `.github/copilot-instructions.md` - This file

As the project develops, maintain a clear directory structure appropriate for the chosen technology stack.

## Security Considerations

Since this is a proxy server that will interface with physical devices (heat pumps):
- Always validate and sanitize inputs from external sources
- Implement proper authentication and authorization
- Secure communication channels (use HTTPS/TLS where appropriate)
- Log security-relevant events
- Follow security best practices for the chosen technology stack
- Be cautious with device control commands to prevent damage to physical equipment

## CI/CD

No continuous integration is set up yet. When adding CI/CD:
- Ensure all tests pass before allowing merges
- Run security scanning on dependencies
- Validate code quality and linting
- Consider adding automated deployment for releases

## Additional Notes

- This is a new project - be prepared to establish conventions and patterns
- Document decisions and patterns as they are established
- The README should be updated as major features are added
- Consider adding CONTRIBUTING.md when the project structure is more established
