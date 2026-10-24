# Student Resource Cloud Vault

A cloud-based academic resource management application built using Flask and AWS. It allows students to browse, search, filter, and download academic resources, while integrating Amazon S3 for file storage, Amazon DynamoDB for resource metadata, and Amazon SNS for email notifications.

## Features

- Browse academic resources through a centralized catalogue.
- Search and filter resources by subject.
- Download available PDF resources.
- Share academic resources through the web interface.
- Store resource files in Amazon S3.
- Manage resource metadata using Amazon DynamoDB.
- Send resource-sharing notifications using Amazon SNS.
- Access the application through a Flask-based web interface.

## Tech Stack

- **Backend:** Python, Flask
- **AWS SDK:** Boto3
- **Storage:** Amazon S3
- **Database:** Amazon DynamoDB
- **Notifications:** Amazon SNS
- **Frontend:** HTML, CSS, JavaScript
- **Version Control:** Git and GitHub

## Architecture

The application follows a backend-driven architecture. The browser communicates with the Flask application, which interacts with AWS services using Boto3.

- **Amazon S3:** Stores academic resource files.
- **Amazon DynamoDB:** Stores resource details and metadata.
- **Amazon SNS:** Publishes notifications to subscribed email addresses.
- **Flask:** Handles application routes, resource retrieval, and download requests.

## Prerequisites

Before running the project, ensure you have:

- Python 3.10 or a compatible version.
- pip installed.
- An AWS account or access to a configured AWS lab environment.
- An existing S3 bucket, DynamoDB table, and SNS topic.
- AWS credentials with the required permissions.

## Installation and Setup

### 1. Clone the repository

```bash
git clone https://github.com/akshiy1804/student-resource-cloud-vault.git
cd student-resource-cloud-vault
```

### 2. Create a virtual environment

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux/macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

If the repository includes a `.env.example` file, copy it to `.env` and configure the required non-secret application settings.

### 4. Configure AWS

Set the AWS region and the names of your existing AWS resources using the environment variable names supported by the application.

Example for Windows PowerShell:

```powershell
$env:AWS_REGION="us-east-1"
$env:DDB_TABLE="StudentResources"
$env:S3_BUCKET="your-s3-bucket-name"
$env:SNS_TOPIC_NAME="StudentResourceVaultNotifications"
```

Configure AWS credentials through your AWS environment or temporary lab credentials:

```powershell
$env:AWS_ACCESS_KEY_ID="your-access-key"
$env:AWS_SECRET_ACCESS_KEY="your-secret-key"
$env:AWS_SESSION_TOKEN="your-session-token"
```

Use the session token when required by temporary AWS credentials. Never commit credentials or secret values to GitHub.

Ensure the configured identity has the necessary permissions to access DynamoDB, S3, and SNS.

### 5. Run the application

```bash
python app.py
```

Open the local URL displayed by Flask, usually:

```text
http://127.0.0.1:5000
```

If the repository provides a separate production-style server script, use its documented command and port instead.

## AWS Resource Configuration

| AWS Service | Resource | Purpose |
|---|---|---|
| Amazon DynamoDB | `StudentResources` | Stores academic resource metadata. |
| Amazon S3 | Configured S3 bucket | Stores resource files. |
| Amazon SNS | `StudentResourceVaultNotifications` | Publishes notifications about shared resources. |

The application expects these AWS resources to exist and be configured correctly. Resource names, table attributes, bucket keys, and permissions must match the actual AWS environment.

## Application Usage

1. Open the application in a browser.
2. Browse the available academic resources.
3. Use the search and subject filters to locate relevant materials.
4. Select an available resource to download its file.
5. Use the resource-sharing interface to submit a resource where sharing is enabled.
6. Check the subscribed email inbox for SNS notifications when a notification is successfully published and delivered.

## Security

- Keep AWS credentials out of source code and version control.
- Use least-privilege IAM permissions.
- Keep the S3 bucket private where appropriate.
- Validate uploaded files and restrict resource-sharing operations to authorized users.
- Use HTTPS and secure environment-variable management for hosted deployments.

## Testing and Troubleshooting

Verify that the AWS credentials are valid and that the configured resources exist before testing cloud operations.

Common issues include:

- **AWS authentication errors:** Check credential validity, session expiry, and region settings.
- **Access denied:** Verify IAM permissions for the required AWS services.
- **Resource not found:** Confirm that DynamoDB metadata matches the actual S3 object key.
- **Notification failure:** Check the SNS topic configuration, publish permissions, and email subscription status.
- **Download failure:** Confirm that the object exists in the configured bucket and that the application has permission to access it.

## Repository and Deployment

**GitHub:** https://github.com/akshiy1804/student-resource-cloud-vault

**Web interface:** https://student-resource-cloud-vault.onrender.com/

The hosted interface and AWS-backed operations depend on the deployment environment and its AWS configuration. AWS lab credentials may expire, so a persistent deployment should use an appropriate, securely managed AWS identity.

## Future Improvements

- Add student authentication and role-based access control.
- Improve upload validation and resource management.
- Add audit logging and automated tests.
- Improve deployment monitoring and error reporting.
- Optimize resource retrieval as the catalogue grows.

