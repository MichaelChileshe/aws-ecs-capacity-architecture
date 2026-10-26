"""awsdiag — tiny helper for drawing architecture diagrams with the OFFICIAL AWS Architecture Icons.

The icon package itself is NOT redistributed. Point AWS_ICONS_DIR at an unzipped copy of the
AWS Architecture Icons asset package (https://aws.amazon.com/architecture/icons/). Icons are
embedded into the rendered SVG/PNG as data URIs, which is the use AWS permits (architecture
diagrams).

Group styling follows the AWS Architecture Icons guidelines:
  AWS Cloud  #242F3E solid · Region #00A4A6 dashed · VPC #8C4FFF solid ·
  Private subnet fill #E6F6F7 (label #00A4A6) · Public subnet fill #F2F6E8 (label #7AA116) ·
  AWS Account #E7157B solid
"""
import base64, glob, os, subprocess

ICONS_DIR = os.environ.get("AWS_ICONS_DIR", os.path.expanduser("~/aws-icons"))
FONT = "Amazon Ember, DejaVu Sans, Arial, Helvetica, sans-serif"

# key -> glob pattern inside the icon package (dated folder names are globbed)
ICONS = {
    # services (64px, coloured square)
    "ec2": "Architecture-Service-Icons_*/Arch_Compute/64/Arch_Amazon-EC2_64.svg",
    "lambda": "Architecture-Service-Icons_*/Arch_Compute/64/Arch_AWS-Lambda_64.svg",
    "s3": "Architecture-Service-Icons_*/Arch_Storage/64/Arch_Amazon-Simple-Storage-Service_64.svg",
    "dynamodb": "Architecture-Service-Icons_*/Arch_Databases/64/Arch_Amazon-DynamoDB_64.svg",
    "ssm": "Architecture-Service-Icons_*/Arch_Management-Tools/64/Arch_AWS-Systems-Manager_64.svg",
    "cloudwatch": "Architecture-Service-Icons_*/Arch_Management-Tools/64/Arch_Amazon-CloudWatch_64.svg",
    "cloudtrail": "Architecture-Service-Icons_*/Arch_Management-Tools/64/Arch_AWS-CloudTrail_64.svg",
    "organizations": "Architecture-Service-Icons_*/Arch_Management-Tools/64/Arch_AWS-Organizations_64.svg",
    "secrets": "Architecture-Service-Icons_*/Arch_Security-Identity/64/Arch_AWS-Secrets-Manager_64.svg",
    "iam": "Architecture-Service-Icons_*/Arch_Security-Identity/64/Arch_AWS-Identity-and-Access-Management_64.svg",
    "kms": "Architecture-Service-Icons_*/Arch_Security-Identity/64/Arch_AWS-Key-Management-Service_64.svg",
    "privatelink": "Architecture-Service-Icons_*/Arch_Networking-Content-Delivery/64/Arch_AWS-PrivateLink_64.svg",
    "elb": "Architecture-Service-Icons_*/Arch_Networking-Content-Delivery/64/Arch_Elastic-Load-Balancing_64.svg",
    "vpc": "Architecture-Service-Icons_*/Arch_Networking-Content-Delivery/64/Arch_Amazon-Virtual-Private-Cloud_64.svg",
    "ram": "Architecture-Service-Icons_*/Arch_Security-Identity/64/Arch_AWS-Resource-Access-Manager_64.svg",
    "identity_center": "Architecture-Service-Icons_*/Arch_Security-Identity/64/Arch_AWS-IAM-Identity-Center_64.svg",
    "sns": "Architecture-Service-Icons_*/Arch_Application-Integration/64/Arch_Amazon-Simple-Notification-Service_64.svg",
    "sqs": "Architecture-Service-Icons_*/Arch_Application-Integration/64/Arch_Amazon-Simple-Queue-Service_64.svg",
    "ec2_autoscaling": "Architecture-Service-Icons_*/Arch_Compute/64/Arch_Amazon-EC2-Auto-Scaling_64.svg",
    "step_functions": "Architecture-Service-Icons_*/Arch_Application-Integration/64/Arch_AWS-Step-Functions_64.svg",
    "eventbridge": "Architecture-Service-Icons_*/Arch_Application-Integration/64/Arch_Amazon-EventBridge_64.svg",
    "aurora": "Architecture-Service-Icons_*/Arch_Databases/64/Arch_Amazon-Aurora_64.svg",
    "app_autoscaling": "Architecture-Service-Icons_*/Arch_Management-Tools/64/Arch_AWS-Application-Auto-Scaling_64.svg",
    "aurora_pg": "Resource-Icons_*/Res_Databases/Res_Amazon-Aurora-PostgreSQL-Instance_48.svg",
    "aurora_pg_alt": "Resource-Icons_*/Res_Databases/Res_Amazon-Aurora-PostgreSQL-Instance-Alternate_48.svg",
    "igw": "Resource-Icons_*/Res_Networking-Content-Delivery/Res_Amazon-VPC_Internet-Gateway_48.svg",
    "datasync": "Architecture-Service-Icons_*/Arch_Migration-Modernization/64/Arch_AWS-DataSync_64.svg",
    "datasync_agent": "Resource-Icons_*/Res_Migration*/Res_AWS-Datasync_Agent_48.svg",
    "storage_gateway": "Architecture-Service-Icons_*/Arch_Storage/64/Arch_AWS-Storage-Gateway_64.svg",
    "s3_file_gateway": "Resource-Icons_*/Res_Storage/Res_AWS-Storage-Gateway_Amazon-S3-File-Gateway_48.svg",
    "tape_gateway": "Resource-Icons_*/Res_Storage/Res_AWS-Storage-Gateway_Tape-Gateway_48.svg",
    "transfer_family": "Architecture-Service-Icons_*/Arch_Migration-Modernization/64/Arch_AWS-Transfer-Family_64.svg",
    "sftp": "Resource-Icons_*/Res_Migration*/Res_AWS-Transfer-Family_AWS-SFTP_48.svg",
    "data_transfer_terminal": "Architecture-Service-Icons_*/Arch_Migration-Modernization/64/Arch_AWS-Data-Transfer-Terminal_64.svg",
    "route53": "Architecture-Service-Icons_*/Arch_Networking-Content-Delivery/64/Arch_Amazon-Route-53_64.svg",
    "r53_zone": "Resource-Icons_*/Res_Networking-Content-Delivery/Res_Amazon-Route-53-Hosted-Zone_48.svg",
    "r53_resolver": "Resource-Icons_*/Res_Networking-Content-Delivery/Res_Amazon-Route-53_Resolver_48.svg",
    "vpc_peering": "Resource-Icons_*/Res_Networking-Content-Delivery/Res_Amazon-VPC_Peering-Connection_48.svg",
    "client": "Resource-Icons_*/Res_General-Icons/Res_48_Light/Res_Client_48_Light.svg",
    "globe": "Resource-Icons_*/Res_General-Icons/Res_48_Light/Res_Globe_48_Light.svg",
    "server": "Resource-Icons_*/Res_General-Icons/Res_48_Light/Res_Server_48_Light.svg",
    "efs": "Architecture-Service-Icons_*/Arch_Storage/64/Arch_Amazon-EFS_64.svg",
    "fsx_windows": "Architecture-Service-Icons_*/Arch_Storage/64/Arch_Amazon-FSx-for-WFS_64.svg",
    "fsx_lustre": "Architecture-Service-Icons_*/Arch_Storage/64/Arch_Amazon-FSx-for-Lustre_64.svg",
    "directory_service": "Architecture-Service-Icons_*/Arch_Security-Identity/64/Arch_AWS-Directory-Service_64.svg",
    "managed_ad": "Resource-Icons_*/Res_Security-Identity/Res_AWS-Directory-Service_AWS-Managed-Microsoft-AD_48.svg",
    "dms": "Architecture-Service-Icons_*/Arch_Databases/64/Arch_AWS-Database-Migration-Service_64.svg",
    "dms_task": "Resource-Icons_*/Res_Databases/Res_AWS-Database-Migration-Service_Database-migration-workflow-or-job_48.svg",
    "rds": "Architecture-Service-Icons_*/Arch_Databases/64/Arch_Amazon-RDS_64.svg",
    "aurora_mysql": "Resource-Icons_*/Res_Databases/Res_Amazon-Aurora-MySQL-Instance_48.svg",
    "rds_instance": "Resource-Icons_*/Res_Databases/Res_Amazon-Aurora_Amazon-RDS-Instance_48.svg",
    "database": "Resource-Icons_*/Res_General-Icons/Res_48_Light/Res_Database_48_Light.svg",
    "logs": "Resource-Icons_*/Res_General-Icons/Res_48_Light/Res_Logs_48_Light.svg",
    "efs_fs":"Resource-Icons_*/Res_Storage/Res_Amazon-Elastic-File-System_File-System_48.svg",
    "global_accelerator": "Architecture-Service-Icons_*/Arch_Networking-Content-Delivery/64/Arch_AWS-Global-Accelerator_64.svg",
    "cloudfront": "Architecture-Service-Icons_*/Arch_Networking-Content-Delivery/64/Arch_Amazon-CloudFront_64.svg",
    "edge_location": "Resource-Icons_*/Res_Networking-Content-Delivery/Res_Amazon-CloudFront_Edge-Location_48.svg",
    "ecs": "Architecture-Service-Icons_*/Arch_Containers/64/Arch_Amazon-Elastic-Container-Service_64.svg",
    "fargate": "Architecture-Service-Icons_*/Arch_Containers/64/Arch_AWS-Fargate_64.svg",
    "ecs_service": "Resource-Icons_*/Res_Containers/Res_Amazon-Elastic-Container-Service_Service_48.svg",
    "ecs_task": "Resource-Icons_*/Res_Containers/Res_Amazon-Elastic-Container-Service_Task_48.svg",
    "service_connect": "Resource-Icons_*/Res_Containers/Res_Amazon-Elastic-Container-Service_ECS-Service-Connect_48.svg",
    "cloud_map": "Architecture-Service-Icons_*/Arch_Networking-Content-Delivery/64/Arch_AWS-Cloud-Map_64.svg",
    "asg": "Resource-Icons_*/Res_Compute/Res_Amazon-EC2_Auto-Scaling_48.svg",
    "spot": "Resource-Icons_*/Res_Compute/Res_Amazon-EC2_Spot-Instance_48.svg",
    "alb": "Resource-Icons_*/Res_Networking-Content-Delivery/Res_Elastic-Load-Balancing_Application-Load-Balancer_48.svg",
    # resources (48px, glyph only)
    "ec2_instance": "Resource-Icons_*/Res_Compute/Res_Amazon-EC2_Instance_48.svg",
    "s3_bucket": "Resource-Icons_*/Res_Storage/Res_Amazon-Simple-Storage-Service_Bucket_48.svg",
    "ddb_table": "Resource-Icons_*/Res_Databases/Res_Amazon-DynamoDB_Table_48.svg",
    "session_manager": "Resource-Icons_*/Res_Management-Governance/Res_AWS-Systems-Manager_Session-Manager_48.svg",
    "cw_logs": "Resource-Icons_*/Res_Management-Governance/Res_Amazon-CloudWatch_Logs_48.svg",
    "sts": "Resource-Icons_*/Res_Security-Identity/Res_AWS-Identity-Access-Management_AWS-STS_48.svg",
    "iam_role": "Resource-Icons_*/Res_Security-Identity/Res_AWS-Identity-Access-Management_Role_48.svg",
    "temp_creds": "Resource-Icons_*/Res_Security-Identity/Res_AWS-Identity-Access-Management_Temporary-Security-Credential_48.svg",
    "long_creds": "Resource-Icons_*/Res_Security-Identity/Res_AWS-Identity-Access-Management_Long-Term-Security-Credential_48.svg",
    "access_analyzer": "Resource-Icons_*/Res_Security-Identity/Res_AWS-Identity-Access-Management_IAM-Access-Analyzer_48.svg",
    "iam_permissions": "Resource-Icons_*/Res_Security-Identity/Res_AWS-Identity-Access-Management_Permissions_48.svg",
    "sns_topic": "Resource-Icons_*/Res_Application-Integration/Res_Amazon-Simple-Notification-Service_Topic_48.svg",
    "sqs_queue": "Resource-Icons_*/Res_Application-Integration/Res_Amazon-Simple-Queue-Service_Queue_48.svg",
    "sqs_message": "Resource-Icons_*/Res_Application-Integration/Res_Amazon-Simple-Queue-Service_Message_48.svg",
    "param_store": "Resource-Icons_*/Res_Management-Governance/Res_AWS-Systems-Manager_Parameter-Store_48.svg",
    "cw_alarm": "Resource-Icons_*/Res_Management-Governance/Res_Amazon-CloudWatch_Alarm_48.svg",
    "eb_bus": "Resource-Icons_*/Res_Application-Integration/Res_Amazon-EventBridge_Custom-Event-Bus_48.svg",
    "eb_rule": "Resource-Icons_*/Res_Application-Integration/Res_Amazon-EventBridge_Rule_48.svg",
    "eb_event": "Resource-Icons_*/Res_Application-Integration/Res_Amazon-EventBridge-Event_48.svg",
    "lambda_fn": "Resource-Icons_*/Res_Compute/Res_AWS-Lambda_Lambda-Function_48.svg",
    "vpc_endpoints": "Resource-Icons_*/Res_Networking-Content-Delivery/Res_Amazon-VPC_Endpoints_48.svg",
    "vpc_router": "Resource-Icons_*/Res_Networking-Content-Delivery/Res_Amazon-VPC_Router_48.svg",
    "nlb": "Resource-Icons_*/Res_Networking-Content-Delivery/Res_Elastic-Load-Balancing_Network-Load-Balancer_48.svg",
    "internet": "Resource-Icons_*/Res_General-Icons/Res_48_Light/Res_Internet_48_Light.svg",
    "user": "Resource-Icons_*/Res_General-Icons/Res_48_Light/Res_User_48_Light.svg",
    "users": "Resource-Icons_*/Res_General-Icons/Res_48_Light/Res_Users_48_Light.svg",
    "office": "Resource-Icons_*/Res_General-Icons/Res_48_Light/Res_Office-building_48_Light.svg",
    # groups (32px corner icons)
    "g_cloud": "Architecture-Group-Icons_*/AWS-Cloud-logo_32.svg",
    "g_region": "Architecture-Group-Icons_*/Region_32.svg",
    "g_vpc": "Architecture-Group-Icons_*/Virtual-private-cloud-VPC_32.svg",
    "g_private": "Architecture-Group-Icons_*/Private-subnet_32.svg",
    "g_public": "Architecture-Group-Icons_*/Public-subnet_32.svg",
    "g_account": "Architecture-Group-Icons_*/AWS-Account_32.svg",
    "g_corp_dc": "Architecture-Group-Icons_*/Corporate-data-center_32.svg",
}

GROUPS = {  # kind: (icon, border, dashed, fill, label colour)
    "cloud":   ("g_cloud",   "#242F3E", False, "none",    "#242F3E"),
    "region":  ("g_region",  "#00A4A6", True,  "none",    "#00A4A6"),
    "vpc":     ("g_vpc",     "#8C4FFF", False, "none",    "#8C4FFF"),
    "private": ("g_private", "none",    False, "#E6F6F7", "#00A4A6"),
    "public":  ("g_public",  "none",    False, "#F2F6E8", "#7AA116"),
    "account": ("g_account", "#E7157B", False, "none",    "#E7157B"),
    "corp_dc": ("g_corp_dc", "#7D8998", False, "none",    "#5A6B86"),
}

INK, GREY = "#232F3E", "#545B64"
_cache = {}


def _uri(key):
    if key not in _cache:
        hits = glob.glob(os.path.join(ICONS_DIR, ICONS[key]))
        if not hits:
            raise FileNotFoundError(f"icon '{key}' not found under {ICONS_DIR} ({ICONS[key]})")
        _cache[key] = "data:image/svg+xml;base64," + base64.b64encode(open(hits[0], "rb").read()).decode()
    return _cache[key]


class Diagram:
    def __init__(self, w, h):
        self.w, self.h, self.out = w, h, []
        self.colors = set()

    def raw(self, s):
        self.out.append(s)

    def icon(self, key, x, y, size=48):
        u = _uri(key)
        self.raw(f'<image x="{x}" y="{y}" width="{size}" height="{size}" href="{u}" xlink:href="{u}"/>')

    def text(self, x, y, t, size=13, weight="normal", color=INK, anchor="start"):
        self.raw(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
                 f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{t}</text>')

    def group(self, kind, x, y, w, h, label):
        ic, border, dashed, fill, lc = GROUPS[kind]
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        stroke = f' stroke="{border}" stroke-width="1.6"' if border != "none" else ""
        self.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"{stroke}{dash}/>')
        self.icon(ic, x, y, 32)
        self.text(x + 40, y + 21, label, 13.5, "bold", lc)

    def box(self, x, y, w, h, stroke, fill="#fff", dashed=True, rx=4):
        dash = ' stroke-dasharray="4 3"' if dashed else ""
        self.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="1.2"{dash}/>')

    def line(self, pts, color, dashed=False, width=2, arrow=True):
        self.colors.add(color)
        d = "M" + " L".join(f"{a} {b}" for a, b in pts)
        dash = ' stroke-dasharray="8 6"' if dashed else ""
        mk = f' marker-end="url(#arr-{color[1:]})"' if arrow else ""
        self.raw(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{dash}{mk}/>')

    def label(self, x, y, t, color=INK, size=11.5):
        w = 6.9 * len(t) * size / 12 + 12
        self.raw(f'<rect x="{x}" y="{y-13}" width="{w:.0f}" height="18" rx="3" fill="#fff" opacity="0.93"/>')
        self.text(x + 6, y, t, size, "normal", color)

    def block(self, x, y, color):
        """A red 'blocked' marker (circle with X)."""
        self.raw(f'<circle cx="{x}" cy="{y}" r="13" fill="#fff" stroke="{color}" stroke-width="2.5"/>'
                 f'<path d="M{x-7} {y-7} L{x+7} {y+7} M{x+7} {y-7} L{x-7} {y+7}" stroke="{color}" stroke-width="3"/>')

    def svg(self):
        defs = "".join(
            f'<marker id="arr-{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="{c}"/></marker>'
            for c in sorted(self.colors))
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}">'
                f'<defs>{defs}</defs><rect width="{self.w}" height="{self.h}" fill="#fff"/>'
                + "\n".join(self.out) + "</svg>")

    def save(self, svg_path, png_path=None):
        s = self.svg()
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(s)
        if png_path:
            html = png_path + ".html"
            with open(html, "w", encoding="utf-8") as f:
                f.write('<html><head><meta charset="utf-8"></head><body style="margin:0">'
                        + s.split("\n", 1)[1] + "</body></html>")
            subprocess.run(["wkhtmltoimage", "--quiet", "--width", str(self.w), "--height", str(self.h),
                            html, png_path], check=False, stderr=subprocess.DEVNULL)
            os.remove(html)
            subprocess.run(["convert", png_path, "-strip", "-define", "png:compression-level=9",
                            png_path], check=False)
