# Simple Cloud - User Management CLI Guide

This guide explains how to use the available CLI commands for managing users in the Simple Cloud application.

## Prerequisites

Before running any commands, make sure to activate your virtual environment:

```sh
source venv/bin/activate
```

## Viewing Available Commands

You can view all available CLI commands along with their descriptions by running:

```sh
flask help
```

## Commands

### Add a User

Adds a new user to the database.

```sh
flask adduser
```

- You will be prompted to enter a username and password.
- If the username already exists, an error message will be displayed.

### Change a User's Password

Updates the password for an existing user.

```sh
flask changepassword
```

- You will be prompted to enter the username and a new password.
- If the user does not exist, an error message will be displayed.

### Change a User's Username

Updates the username of an existing user.

```sh
flask changeusername
```

- You will be prompted to enter the current username and the new username.
- If the current username does not exist, an error message will be displayed.
- If the new username is already taken, an error message will be displayed.
- Uploaded files associated with the user will be updated to reflect the new username.

### Delete a User

Deletes a user from the database and provides options for handling their uploaded files.

```sh
flask deleteuser
```

- You will be prompted to enter the username to delete.
- If the user exists, you will have the option to:
  - Transfer their files to another existing user.
  - Create a new user to transfer the files to.
  - Delete all files uploaded by the user.

### List All Users

Displays a list of all registered users.

```sh
flask listusers
```

- Shows user IDs and usernames.
- If no users are found, a message is displayed.

---

After running any of these commands, the database is optimized by running the `VACUUM;` command.

For further details, use `flask helpme` to get descriptions of each command directly in the terminal.

