"""aws-sm:// backend: AWS Secrets Manager via boto3.

aws-sm://<name-or-arn> maps to SecretId. Credentials and region come from the
standard AWS environment (env vars, profile, instance role).
"""

from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from interlock.vault import VaultError

if TYPE_CHECKING:
    from types_boto3_secretsmanager import SecretsManagerClient

PREFIX = "aws-sm://"


def secret_id(ref: str) -> str:
    return ref.removeprefix(PREFIX)


def _code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "Unknown")


class AwsSecretsManagerBackend:
    def __init__(self, client: "SecretsManagerClient | None" = None) -> None:
        # boto3.client is an overload over every AWS service; only this one is typed here.
        self._client: SecretsManagerClient = client or boto3.client("secretsmanager")  # pyright: ignore[reportUnknownMemberType]

    def get(self, ref: str) -> str:
        try:
            response = self._client.get_secret_value(SecretId=secret_id(ref))
        except ClientError as error:
            # Never include the response body: only the code is safe to show.
            raise VaultError(f"cannot read secret_ref '{ref}' from AWS: {_code(error)}") from None
        except BotoCoreError as error:
            raise VaultError(
                f"cannot reach AWS Secrets Manager for '{ref}': {type(error).__name__}"
            ) from None
        if "SecretString" not in response:
            raise VaultError(f"secret_ref '{ref}' is a binary secret; only strings are supported")
        return response["SecretString"]

    def put(self, ref: str, value: str) -> None:
        name = secret_id(ref)
        try:
            try:
                self._client.put_secret_value(SecretId=name, SecretString=value)
            except ClientError as error:
                if _code(error) != "ResourceNotFoundException":
                    raise
                self._client.create_secret(Name=name, SecretString=value)
        except ClientError as error:
            raise VaultError(f"cannot store secret_ref '{ref}' in AWS: {_code(error)}") from None
        except BotoCoreError as error:
            raise VaultError(
                f"cannot reach AWS Secrets Manager for '{ref}': {type(error).__name__}"
            ) from None
