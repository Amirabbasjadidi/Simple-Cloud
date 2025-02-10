# Simple Cloud

## Overview
This is a Flask-based Cloud with authentication, user management, and file handling capabilities. The application allows users to upload, download, and manage files securely.

## Features
- User authentication with Flask-Login
- Secure file uploads and downloads
- User management through CLI
- API for retrieving and deleting user files
- Responsive and user-friendly design
- Default user `root` with password `1234` (must be changed after setup)

## License
This project is licensed under the GNU General Public License v3.0 (GPLv3). See the [`License`](https://github.com/Amirabbasjadidi/Simple-Cloud/blob/main/LICENSE) file for details.

## Installation
For installation instructions, refer to the [`How to install.md`](./How%20to%20install.md) file.

## User Management
For managing users through the command-line interface (CLI), refer to the [`User management cli.md`](./User%20management%20cli.md) file.

## Security Notice
- The default user `root` is created with the password `1234`. It is strongly recommended to change the password immediately after setup using the CLI.
- If the username `root` is changed, the system will recreate `root` with the default password upon restart.
- Always ensure secure password management practices.

## API Endpoints
- `POST /upload` - Upload a file
- `POST /download` - Download a file from a URL
- `GET /api/files` - List uploaded files
- `DELETE /api/files/<file_id>` - Delete a file

For more details, refer to the source code.

## Contributions
Contributions are welcome! Please adhere to the GPLv3 license and submit pull requests for any improvements or bug fixes.

## Support
If you find this project useful, please consider starring it on GitHub to support further development!

## Contact
For issues or feature requests, open a GitHub issue or contact me through one of the methods listed on my website: [My Website](https://amirabbasjadidi.ir/).

