"""Seed curated vendor and product entities into entity_intel

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-05-19
"""
from typing import Sequence, Union
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VENDORS = [
    ("amd", "AMD", ["AMD"]),
    ("aws", "AWS", ["Amazon Web Services", "AWS"]),
    ("cloudflare", "Cloudflare", ["Cloudflare"]),
    ("github", "GitHub", ["GitHub"]),
    ("hp", "HP", ["HP", "Hewlett-Packard"]),
    ("huawei", "Huawei", ["Huawei"]),
    ("lenovo", "Lenovo", ["Lenovo"]),
    ("nvidia", "NVIDIA", ["NVIDIA", "Nvidia"]),
    ("openai", "OpenAI", ["OpenAI"]),
    ("signal", "Signal", ["Signal"]),
    ("telegram", "Telegram", ["Telegram"]),
    ("whatsapp", "WhatsApp", ["WhatsApp"]),
    ("zoom", "Zoom", ["Zoom"]),
]

_PRODUCTS = [
    ("active-directory", "Active Directory", ["Active Directory", "AD"]),
    ("android", "Android", ["Android"]),
    ("ansible", "Ansible", ["Ansible"]),
    ("apache-http-server", "Apache HTTP Server", ["Apache HTTP Server", "Apache httpd"]),
    ("apparmor", "AppArmor", ["AppArmor"]),
    ("azure-ad", "Azure AD", ["Azure AD", "Azure Active Directory"]),
    ("bamboo", "Bamboo", ["Bamboo"]),
    ("bitbucket", "Bitbucket", ["Bitbucket"]),
    ("chrome", "Chrome", ["Google Chrome"]),
    ("chromium", "Chromium", ["Chromium"]),
    ("cisco-asa", "Cisco ASA", ["Cisco ASA"]),
    ("cisco-duo", "Cisco Duo", ["Cisco Duo", "Duo"]),
    ("citrix-adc", "Citrix ADC", ["Citrix ADC"]),
    ("citrix-workspace", "Citrix Workspace", ["Citrix Workspace"]),
    ("confluence", "Confluence", ["Confluence"]),
    ("cortex-xdr", "Cortex XDR", ["Cortex XDR"]),
    ("docker", "Docker", ["Docker"]),
    ("entra-id", "Entra ID", ["Entra ID", "Microsoft Entra ID"]),
    ("esxi", "ESXi", ["ESXi", "VMware ESXi"]),
    ("exchange", "Exchange", ["Microsoft Exchange", "Exchange Server"]),
    ("firepower", "Firepower", ["Firepower", "Cisco Firepower"]),
    ("fortiadc", "FortiADC", ["FortiADC"]),
    ("fortianalyzer", "FortiAnalyzer", ["FortiAnalyzer"]),
    ("forticlient", "FortiClient", ["FortiClient"]),
    ("fortigate", "FortiGate", ["FortiGate"]),
    ("fortimanager", "FortiManager", ["FortiManager"]),
    ("fortios", "FortiOS", ["FortiOS"]),
    ("fortiproxy", "FortiProxy", ["FortiProxy"]),
    ("fortisiem", "FortiSIEM", ["FortiSIEM"]),
    ("fortiswitch", "FortiSwitch", ["FortiSwitch"]),
    ("fortiweb", "FortiWeb", ["FortiWeb"]),
    ("globalprotect", "GlobalProtect", ["GlobalProtect"]),
    ("google-cloud", "Google Cloud", ["Google Cloud", "GCP"]),
    ("ios", "iOS", ["iOS"]),
    ("ios-xe", "IOS XE", ["IOS XE", "Cisco IOS XE"]),
    ("ios-xr", "IOS XR", ["IOS XR", "Cisco IOS XR"]),
    ("ipados", "iPadOS", ["iPadOS"]),
    ("ivanti-connect-secure", "Ivanti Connect Secure", ["Ivanti Connect Secure", "Pulse Connect Secure"]),
    ("ivanti-epmm", "Ivanti EPMM", ["Ivanti EPMM"]),
    ("jenkins", "Jenkins", ["Jenkins"]),
    ("jira", "Jira", ["Jira"]),
    ("juniper-srx", "Juniper SRX", ["Juniper SRX", "SRX Series"]),
    ("junos", "Junos", ["Junos", "Juniper Junos"]),
    ("kubernetes", "Kubernetes", ["Kubernetes", "K8s"]),
    ("macos", "macOS", ["macOS", "Mac OS X"]),
    ("meraki", "Meraki", ["Meraki", "Cisco Meraki"]),
    ("microsoft-365", "Microsoft 365", ["Microsoft 365", "M365"]),
    ("microsoft-defender", "Microsoft Defender", ["Microsoft Defender"]),
    ("microsoft-edge", "Microsoft Edge", ["Microsoft Edge"]),
    ("netscaler", "NetScaler", ["NetScaler", "Citrix NetScaler"]),
    ("nginx", "Nginx", ["Nginx", "NGINX"]),
    ("openssh", "OpenSSH", ["OpenSSH"]),
    ("openssl", "OpenSSL", ["OpenSSL"]),
    ("outlook", "Outlook", ["Outlook", "Microsoft Outlook"]),
    ("pan-os", "PAN-OS", ["PAN-OS"]),
    ("panorama", "Panorama", ["Panorama", "Palo Alto Panorama"]),
    ("pulse-connect-secure", "Pulse Connect Secure", ["Pulse Connect Secure"]),
    ("safari", "Safari", ["Safari"]),
    ("sharepoint", "SharePoint", ["SharePoint", "Microsoft SharePoint"]),
    ("sonicos", "SonicOS", ["SonicOS"]),
    ("terraform", "Terraform", ["Terraform"]),
    ("vcenter", "vCenter", ["vCenter", "VMware vCenter"]),
    ("vmware-workstation", "VMware Workstation", ["VMware Workstation"]),
    ("vsphere", "vSphere", ["vSphere", "VMware vSphere"]),
    ("watchos", "watchOS", ["watchOS"]),
    ("webex", "Webex", ["Webex", "Cisco Webex"]),
    ("webkit", "WebKit", ["WebKit"]),
    ("windows", "Windows", ["Microsoft Windows"]),
    ("windows-server", "Windows Server", ["Windows Server", "Microsoft Windows Server"]),
    ("wing-ftp", "Wing FTP", ["Wing FTP"]),
    ("xenserver", "XenServer", ["XenServer", "Citrix XenServer"]),
]


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    for normalized_key, display_name, aliases in _VENDORS:
        conn.execute(
            sa.text("""
                INSERT INTO entity_intel (normalized_key, display_name, entity_type, aliases, source, source_id, active, last_synced)
                VALUES (:key, :name, 'vendor', CAST(:aliases AS jsonb), 'curated', NULL, true, :now)
                ON CONFLICT (normalized_key) DO NOTHING
            """),
            {"key": normalized_key, "name": display_name, "aliases": __import__("json").dumps(aliases), "now": now},
        )

    for normalized_key, display_name, aliases in _PRODUCTS:
        conn.execute(
            sa.text("""
                INSERT INTO entity_intel (normalized_key, display_name, entity_type, aliases, source, source_id, active, last_synced)
                VALUES (:key, :name, 'product', CAST(:aliases AS jsonb), 'curated', NULL, true, :now)
                ON CONFLICT (normalized_key) DO NOTHING
            """),
            {"key": normalized_key, "name": display_name, "aliases": __import__("json").dumps(aliases), "now": now},
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM entity_intel WHERE source = 'curated'")
    )
