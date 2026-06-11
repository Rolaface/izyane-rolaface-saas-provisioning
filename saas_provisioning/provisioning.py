from datetime import datetime
import frappe
import subprocess
from saas_provisioning.dns import add_caddy_domain

from frappe.desk.page.setup_wizard.setup_wizard import get_setup_stages, parse_args, process_setup_stages, sanitize_input

MARIADB_ROOT_USERNAME = "root"
MARIADB_ROOT_PASSWORD = "root123"


def create_site_job(site_name, db_name, payload):
    bench_path = frappe.utils.get_bench_path()

    print(f"🚀 Starting site creation for {site_name}")
    frappe.logger().info(f"Starting site creation for {site_name}")

    try:
        # 1️⃣ Create site using bench command
        cmd = [
            "/home/frappe/.local/bin/bench", "new-site", site_name,
            "--db-name", db_name,
            "--admin-password", "root",
            "--install-app", "erpnext",
            "--install-app", "auth_api",
            "--install-app", "custom_api",
            "--install-app", "custom_hrms",
            "--install-app", "hrms",
            "--mariadb-user-host-login-scope=%",
            "--mariadb-root-username", MARIADB_ROOT_USERNAME,
            "--mariadb-root-password", MARIADB_ROOT_PASSWORD,
        ]

        # for app in payload.get("apps", []):
        #     cmd.extend(["--install-app", app])

        print(f"📝 Running bench new-site for {site_name}")

        result = subprocess.run(
            cmd,
            cwd=bench_path,
            check=True,
            capture_output=True,
            text=True,
            timeout=3600
        )

        if result.stdout:
            print(f"✅ Bench output:\n{result.stdout}")
        if result.stderr:
            print(f"⚠️  Bench stderr:\n{result.stderr}")

        print(f"✅ Site {site_name} created successfully")
        frappe.logger().info(f"Site {site_name} created successfully")

        # 1️⃣a Run migrate command to apply custom fields from auth_api and custom_api
        # print(f"🔄 Running migrate for {site_name}...")
        # migrate_cmd = [
        #     "/home/frappe/.local/bin/bench", "--site", site_name, "migrate"
        # ]

        # try:
        #     migrate_result = subprocess.run(
        #         migrate_cmd,
        #         cwd=bench_path,
        #         check=True,
        #         capture_output=True,
        #         text=True,
        #         timeout=600  # 10 minute timeout for migrate
        #     )

        #     if migrate_result.stdout:
        #         print(f"✅ Migrate output:\n{migrate_result.stdout}")
        #     if migrate_result.stderr and "error" in migrate_result.stderr.lower():
        #         print(f"⚠️  Migrate stderr:\n{migrate_result.stderr}")

        #     print(f"✅ Migrate completed for {site_name}")
        #     frappe.logger().info(f"Migrate completed for {site_name}")

        # except subprocess.CalledProcessError as migrate_error:
        #     error_msg = f"Migrate command failed: {migrate_error.stderr}"
        #     print(f"⚠️  Warning: {error_msg}")
        #     frappe.logger().warning(error_msg)
        #     # Don't fail the job — continue with setup wizard
        #     # Custom fields will be applied during setup wizard if needed

        # 3️⃣ Initialize site context
        frappe.init(site=site_name, force=True)
        frappe.connect()
        frappe.set_user("Administrator")
        frappe.clear_cache()

        # 4️⃣ Check if setup already complete
        if frappe.db.get_single_value("System Settings", "setup_complete"):
            print(f"⚠️  Setup already completed for {site_name}, skipping.")
            # add_caddy_domain(site_name)
            return {"status": "ok"}

        # Don't run setup in background — run it synchronously and wait for completion
        # Set default fiscal year dates based on country
        import datetime
        current_year = datetime.datetime.now().year
        
        # Timezone mapping for deprecated/old timezone names
        timezone_mapping = {
            "Asia/Calcutta": "Asia/Kolkata",
            "Asia/Kathmandu": "Asia/Kathmandu",
            "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
            "Australia/Melbourne": "Australia/Melbourne",
            "Australia/Sydney": "Australia/Sydney",
        }
        
        # Get timezone from payload and apply mapping if needed
        raw_timezone = payload.get("timezone") or "UTC"
        timezone = timezone_mapping.get(raw_timezone, raw_timezone)
        
        if raw_timezone != timezone:
            print(f"⚠️  Timezone '{raw_timezone}' mapped to '{timezone}'")
        
        # Get fiscal year start month from payload (REQUIRED FIELD)
        country = payload.get("country") or "United States"
        fy_start_month = payload.get("fy_start_month") or "04"  # Default to April if not provided
        
        # Validate that fy_start_month is provided
        if not fy_start_month:
            error_msg = "❌ fy_start_month is a required field (1-12)"
            print(error_msg)
            frappe.log_error(error_msg, "Missing Fiscal Year Start Month")
            raise ValueError(error_msg)
        
        # Convert to integer if it's a string
        try:
            fy_start_month = int(fy_start_month)
        except (ValueError, TypeError):
            error_msg = f"❌ fy_start_month must be an integer between 1-12, got: {fy_start_month}"
            print(error_msg)
            frappe.log_error(error_msg, "Invalid Fiscal Year Start Month")
            raise ValueError(error_msg)
        
        # Validate month is between 1-12
        if not (1 <= fy_start_month <= 12):
            error_msg = f"❌ fy_start_month must be between 1-12, got: {fy_start_month}"
            print(error_msg)
            frappe.log_error(error_msg, "Invalid Fiscal Year Start Month")
            raise ValueError(error_msg)
        
        # Calculate fiscal year start and end dates based on start month
        # If fiscal year starts in current month or later, it starts this year
        # Otherwise, it started last year
        today = datetime.datetime.now()
        
        if fy_start_month <= today.month:
           fy_start_year = current_year
        else:
            fy_start_year = current_year - 1

        # Calculate end month (one month before start month)
        fy_end_month = (fy_start_month - 2) % 12 + 1

        # End year: Apr→Mar = next year | Jan→Dec = same year
        if fy_end_month < fy_start_month:
            fy_end_year = fy_start_year + 1
        else:
            fy_end_year = fy_start_year

        # Get last day of end month
        if fy_end_month == 12:
            last_day_of_end_month = 31
        else:
            next_month = datetime.datetime(fy_end_year, fy_end_month + 1, 1)
            last_day_of_end_month = (next_month - datetime.timedelta(days=1)).day
        
        # Build dates
        fy_start_date = f"{fy_start_year}-{str(fy_start_month).zfill(2)}-01"
        fy_end_date = f"{fy_end_year}-{str(fy_end_month).zfill(2)}-{str(last_day_of_end_month).zfill(2)}"
        
        print(f"💰 Calculated Fiscal Year: {fy_start_date} to {fy_end_date} (Start month: {fy_start_month})")
        
        setup_payload = {
            "currency": payload.get("currency") or "USD",
            "country": country,
            "timezone": timezone,
            "language": payload.get("language", "en"),
            "full_name": payload.get("full_name"),
            "email": payload.get("email"),
            "password": payload.get("password"),
            "company_name": payload.get("company_name"),
            "company_abbr": payload.get("company_abbr"),
            "chart_of_accounts": "Standard",
            "fy_start_date": fy_start_date,
            "fy_end_date": fy_end_date,
            "setup_demo": payload.get("setup_demo", 0),
        }
        
        print(f"📋 Setup payload (with country-specific defaults):")
        print(f"  Currency: {setup_payload.get('currency')}")
        print(f"  Country: {setup_payload.get('country')}")
        print(f"  Timezone: {setup_payload.get('timezone')}")
        print(f"  Company: {setup_payload.get('company_name')}")
        print(f"  FY Start: {setup_payload.get('fy_start_date')} (start month: {fy_start_month})")
        print(f"  FY End: {setup_payload.get('fy_end_date')}")
        print(f"  Chart of Accounts: {setup_payload.get('chart_of_accounts')}")


        # print(f"🌐 Adding {site_name} to Caddy BEFORE setup wizard...")
        # try:
        #     add_caddy_domain(site_name)
        #     print(f"✅ Caddy domain added: {site_name}")
        # except Exception as caddy_err:
        #     print(f"⚠️  Caddy setup failed (non-fatal): {caddy_err}")
        #     frappe.logger().warning(f"Caddy early setup failed: {caddy_err}")

        # 6️⃣ Run ERPNext setup wizard synchronously
        print(f"🔧 Running setup wizard for {site_name}...")
        kwargs = parse_args(sanitize_input(setup_payload))
        stages = get_setup_stages(kwargs)

        print(f"✅ Going through Setup stages:")
        try:
            # Run setup synchronously — this will complete before proceeding
            process_setup_stages(stages, kwargs)
            print(f"✅ Setup wizard completed successfully for {site_name}")
        except Exception as setup_error:
            print(f"⚠️  Setup wizard encountered an error: {str(setup_error)}")
            frappe.logger().warning(f"Setup wizard error for {site_name}: {str(setup_error)}")
            # Don't fail — continue, the setup may have partially completed
            # and we want the site to be accessible even if setup had issues

        # 5️⃣ Verify setup completion
        print(f"🔍 Verifying setup completion for {site_name}...")
        frappe.init(site=site_name, force=True)
        frappe.connect()
        frappe.set_user("Administrator")
        setup_status = frappe.db.get_single_value("System Settings", "setup_complete")
        print(f"Setup complete status: {setup_status}")
        user_email = payload.get("email")
        if user_email and user_email != "Administrator":
            try:
                if frappe.db.exists("User", user_email):

                    # ── Check if Administrator role exists ────────────────────
                    admin_role_exists = frappe.db.exists("Role", "Administrator")

                    if not admin_role_exists:
                        print(f"⚠️  Administrator role does not exist, skipping.")
                    else:
                        # ── Check if already assigned ─────────────────────────
                        already_assigned = frappe.db.exists("Has Role", {
                            "parent": user_email,
                            "parenttype": "User",
                            "role": "Administrator",
                        })

                        if already_assigned:
                            print(f"✅ Administrator role already assigned to {user_email}")
                        else:
                            # ── Assign the role ───────────────────────────────
                            user_doc = frappe.get_doc("User", user_email)
                            user_doc.append("roles", {"role": "Administrator"})
                            user_doc.save(ignore_permissions=True)
                            frappe.db.commit()

                            print(f"✅ Administrator role assigned to {user_email}")
                            frappe.logger().info(f"Administrator role assigned to {user_email} for site {site_name}")
                else:
                    print(f"⚠️  User {user_email} not found, skipping role assignment.")

            except Exception as role_error:
                print(f"⚠️  Failed to assign Administrator role (non-fatal): {role_error}")
                frappe.logger().warning(f"Role assignment failed for {user_email}: {role_error}")

        if not setup_status:
            print(f"⚠️  Setup not marked as complete, but continuing with provisioning...")

        print(f"🎉 Site provisioning completed successfully!")
        frappe.logger().info(f"Site {site_name} provisioned successfully")

        return {"status": "success", "site": site_name}

    except subprocess.CalledProcessError as e:
        error_msg = f"Bench command failed: {e.stderr}"
        print(f"❌ ERROR: {error_msg}")
        frappe.log_error(error_msg, "Site Creation Failed")
        raise

    except Exception as e:
        import traceback
        error_msg = f"Site setup failed for {site_name}: {str(e)}"
        print(f"❌ ERROR: {error_msg}")
        print(f"📍 Traceback:\n{traceback.format_exc()}")
        frappe.log_error(error_msg, "Site Creation Failed")
        raise


def send_welcome_email(email, site_name, company_name):
    try:
        frappe.sendmail(
            recipients=[email],
            subject=f"Welcome to {company_name} - Your ERP Site is Ready!",
            message=f"""
            <h2>Your ERP site is ready! 🎉</h2>
            <p><strong>Site URL:</strong> <a href="https://{site_name}">https://{site_name}</a></p>
            <p><strong>Username:</strong> {email}</p>
            <p>Best regards,<br>Your ERP Team</p>
            """,
            now=True
        )
        print(f"✅ Welcome email sent to {email}")
    except Exception as e:
        print(f"⚠️  Failed to send welcome email: {str(e)}")