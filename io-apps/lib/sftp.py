import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Union

from paramiko import RejectPolicy, SFTPAttributes, SFTPClient, SSHClient

logger = logging.getLogger(__name__)


@dataclass
class FileInfos:
    sha256: str
    size: int


@dataclass
class FileAttributes:
    size: int
    mtime_dt: datetime


class SFTP_Indexed:
    """
    A class to manage SFTP operations over an SSH connection, with a focus on sha256 file index mangement.
    """

    # regex to match lines like "64_char_sha256_hash  filename"
    SHA_PATTERN = re.compile(r'([0-9a-fA-F]{64})\s+(.+)')

    def __init__(self, hostname: str, username: str, password: Optional[str] = None,
                 port: int = 22, base_dir: Union[str, Path] = '') -> None:
        # private
        self._base_dir = Path(base_dir)
        # args
        self.hostname = hostname
        self.username = username
        self.password = password
        self.port = port
        self.ssh_cli: Optional[SSHClient] = None
        self.sftp_cli: Optional[SFTPClient] = None

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @base_dir.setter
    def base_dir(self, value: Union[str, Path]):
        self._base_dir = Path(value)

    def __enter__(self):
        """
        Establishes the SSH and SFTP connections when entering the 'with' block.
        """
        self.ssh_cli = SSHClient()
        self.ssh_cli.set_missing_host_key_policy(RejectPolicy())
        self.ssh_cli.load_system_host_keys()
        self.ssh_cli.connect(hostname=self.hostname, port=self.port, username=self.username, password=self.password)
        self.sftp_cli = self.ssh_cli.open_sftp()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Closes the SFTP and SSH connections when exiting the 'with' block.
        """
        if self.sftp_cli:
            self.sftp_cli.close()
            self.sftp_cli = None
        if self.ssh_cli:
            self.ssh_cli.close()
            self.ssh_cli = None

    def _check_connection(self):
        """
        Internal helper method to ensure the SFTP client connection is active.

        Raises:
            RuntimeError: If the SFTP client connection is not established.
        """
        if not self.sftp_cli:
            raise RuntimeError("SFTP connection not established. Use 'with MySFTP(...) as sftp:'")

    def _real_path(self, filename: Union[str, Path]) -> str:
        """
        Constructs the full remote path by joining base_dir and filename.
        Ensures the path uses forward slashes.

        Args:
            filename (Union[str, Path]): The filename or relative path

        Returns:
            str: The absolute path on the remote SFTP server, with forward slashes
        """
        # pathlib handles concatenation correctly, then ensure forward slashes
        return str((self.base_dir / filename).as_posix())

    def get_file_attrs(self, remote_filename: Union[str, Path]) -> FileAttributes:
        """
        Retrieves comprehensive information (size, UTC-aware modification time)
        about a single remote file.

        Args:
            remote_filename (Union[str, Path]): The name of the remote file, relative to `base_dir`.

        Returns:
            FileAttributes: An object containing the file's base name, full path, size,
                            and UTC-aware last modification time.

        Raises:
            RuntimeError: If the SFTP client connection is not established.
            FileNotFoundError: If the remote file does not exist.
            paramiko.SFTPError: For other SFTP-related errors (e.g., permissions).
            Exception: For unexpected errors during information retrieval.
        """

        self._check_connection()

        remote_full_path = self._real_path(remote_filename)
        try:
            # sftp.stat() returns an SFTPAttributes object
            sftp_attrs: SFTPAttributes = self.sftp_cli.stat(remote_full_path)
            # st_mtime is the last modification time (Unix timestamp)
            # st_atime is the last access time (Unix timestamp)
            return FileAttributes(size=sftp_attrs.st_size,
                                  mtime_dt=datetime.fromtimestamp(sftp_attrs.st_mtime, tz=timezone.utc))
        except FileNotFoundError:
            raise FileNotFoundError(f'remote file "{remote_full_path}" not found')

    def index_attributes(self, index_filename: Union[str, Path] = 'index.sha256') -> FileAttributes:
        return self.get_file_attrs(index_filename)

    def get_file_as_bytes(self, remote_filename: Union[str, Path]) -> bytes:
        """
        Reads a remote file and returns its content as bytes.

        Args:
            remote_filename (Union[str, Path]): The name of the remote file

        Returns:
            bytes: The content of the file

        Raises:
            FileNotFoundError: if the remote file does not exist
        Raises:
            RuntimeError: if the SFTP connection is not established.
        """

        self._check_connection()

        remote_full_path = self._real_path(remote_filename)
        try:
            # Access self.sftp via the property, which ensures it's open
            with self.sftp_cli.open(remote_full_path, mode='rb') as f:
                return f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f'remote file "{remote_full_path}" not found')

    def get_index_as_dict(self, index_filename: Union[str, Path] = 'index.sha256') -> dict:
        """
        Reads a remote SHA256 index file and parses its contents into a dictionary.

        Args:
            index_filename (Union[str, Path]): The name of the SHA256 index file. Defaults to 'index.sha256'

        Returns:
            Dict[str, str]: A dictionary where keys are filenames and values are
                            their corresponding SHA256 checksums (lowercase)

        Raises:
            FileNotFoundError: if the remote index file does not exist
            UnicodeDecodeError: if the index file content cannot be decoded with UTF-8
        """
        sha256_d: Dict[str, str] = {}

        raw_index_bytes = self.get_file_as_bytes(index_filename)

        for line in raw_index_bytes.decode('utf-8').splitlines():
            # regex to match lines like "sha_hash filename"
            match = self.SHA_PATTERN.match(line.strip())
            if match:
                sha_hash, filename = match.groups()
                sha256_d[filename] = sha_hash.lower()
        return sha256_d

    def close(self):
        self.sftp_cli.close()
        self.ssh_cli.close()
