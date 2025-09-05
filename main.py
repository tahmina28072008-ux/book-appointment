import firebase_admin
from firebase_admin import firestore
from firebase_admin import credentials
from datetime import datetime
import json
import uuid
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Global variables for Firebase configuration.
# The `__app_id`, `__firebase_config`, and `__initial_auth_token` are provided by the canvas environment.
__app_id = "your-app-id"  # Replace with a default ID or leave as a placeholder
__firebase_config = '{}'  # Replace with a placeholder
__initial_auth_token = None  # Replace with a placeholder

# Initialize Firebase
try:
    cred = credentials.Certificate(json.loads(__firebase_config))
    firebase_admin.initialize_app(cred)
    db = firestore.client()
except ValueError:
    print("Firebase config is not valid. The app will use a default setup but will not connect to the database.")
    db = None

# Mock data for demonstration if Firebase is not connected
MOCK_PATIENTS = {
    'PbiVgrmLxGhdcoynZKKFxrXlz373': {
        'name': 'Tahmina',
        'surname': 'Akhtar',
        'dateOfBirth': '1992-03-12',
        'insuranceProvider': 'MedStar Health',
        'policyNumber': 'D123456',
        'email': 'tahmina.akhtar2807@gmail.com',
        'bookings': [],
    }
}

MOCK_DOCTORS = {
    'gp-001': {
        'id': 'gp-001',
        'name': 'Dr. Lucy Morgan, MRCGP',
        'specialty': 'General Practitioner',
        'availability': {
            '2025-09-03': {
                '13:00': True,
                '14:00': True,
            }
        }
    },
    'gp-002': {
        'id': 'gp-002',
        'name': 'Dr. Adam Collins, MRCGP',
        'specialty': 'General Practitioner',
        'availability': {
            '2025-09-03': {
                '10:00': True,
                '11:00': False, # This slot is already booked
            }
        }
    }
}

# Mock data for insurance cost calculation
INSURANCE_RATES = {
    "MedStar Health": {
        "appointment_cost": 150.00,
        "co_pay": 25.00
    },
    "Blue Cross Blue Shield": {
        "appointment_cost": 180.00,
        "co_pay": 30.00
    },
    "default": {
        "appointment_cost": 200.00,
        "co_pay": 50.00
    }
}


def calculate_appointment_cost(insurance_provider: str) -> dict:
    """
    Calculates the appointment cost and patient's co-pay based on insurance.
    """
    rates = INSURANCE_RATES.get(insurance_provider, INSURANCE_RATES["default"])
    total_cost = rates["appointment_cost"]
    co_pay = rates["co_pay"]
    
    return {
        "totalCost": total_cost,
        "patientCopay": co_pay,
        "insuranceClaim": total_cost - co_pay
    }


def validate_and_book(name: str, dob: str, insurance_provider: str, policy_number: str, booking_details: dict) -> str:
    """
    Validates patient and doctor availability, calculates cost, and updates their booking.
    """
    specialty = booking_details.get('specialty')
    doctor_name = booking_details.get('doctorName')
    appointment_date = booking_details.get('appointmentDate')
    appointment_time = booking_details.get('appointmentTime')

    if not all([specialty, doctor_name, appointment_date, appointment_time]):
        return "Error: Incomplete booking details provided."

    app_id = __app_id if __app_id and __app_id != 'your-app-id' else 'default-app-id'

    try:
        # Step 1: Check doctor's availability
        is_available = False
        doctor_ref = None

        if db:
            doctors_ref = db.collection('artifacts').document(app_id).collection('doctors')
            query_results = doctors_ref.where('specialty', '==', specialty)\
                                       .where('name', '==', doctor_name).limit(1).stream()
            
            for doc in query_results:
                doctor_ref = doc.reference
                availability = doc.get('availability') or {}
                
                if availability.get(appointment_date, {}).get(appointment_time):
                    is_available = True
                break
            
            if not is_available:
                return f"Error: The selected doctor ({doctor_name}) is not available on {appointment_date} at {appointment_time}."
        else:
            mock_doctor = next((d for d in MOCK_DOCTORS.values() if d['name'] == doctor_name), None)
            if mock_doctor and mock_doctor['availability'].get(appointment_date, {}).get(appointment_time):
                is_available = True
            if not is_available:
                return f"Error: The selected doctor ({doctor_name}) is not available on {appointment_date} at {appointment_time}."

        # Step 2: Validate patient details and update booking
        patient_doc = None
        if db:
            users_ref = db.collection('artifacts').document(app_id).collection('users')
            query_results = users_ref.where('name', '==', name)\
                                     .where('dateOfBirth', '==', dob)\
                                     .where('insuranceProvider', '==', insurance_provider)\
                                     .where('policyNumber', '==', policy_number).limit(1).stream()
            
            for doc in query_results:
                patient_doc = doc
                break

            if not patient_doc:
                return "Error: Patient not found or details do not match."

            cost_details = calculate_appointment_cost(insurance_provider)
            booking_details['costBreakdown'] = cost_details
            
            updates = {
                f"availability.{appointment_date}.{appointment_time}": False,
                "lastUpdated": firestore.SERVER_TIMESTAMP
            }
            doctor_ref.update(updates)

        else:
            patient = MOCK_PATIENTS.get('PbiVgrmLxGhdcoynZKKFxrXlz373')
            if not patient or patient['name'] != name or patient['dateOfBirth'] != dob or \
               patient['insuranceProvider'] != insurance_provider or patient['policyNumber'] != policy_number:
                return "Error: Patient not found or details do not match in mock data."

            cost_details = calculate_appointment_cost(insurance_provider)
            booking_details['costBreakdown'] = cost_details
            
            mock_doctor['availability'][appointment_date][appointment_time] = False
            patient_doc = patient

        patient_email = patient_doc.get('email')
        if not patient_email:
            return "Error: Patient email not found."

        booking_id = f"booking_{int(datetime.now().timestamp() * 1000)}"
        booking_details['bookingId'] = booking_id
        booking_details['createdAt'] = datetime.now().isoformat() + 'Z'
        booking_details['updatedAt'] = datetime.now().isoformat() + 'Z'

        if db:
            doc_ref = patient_doc.reference
            current_bookings = patient_doc.get('bookings') or []
            current_bookings.append(booking_details)
            doc_ref.update({'bookings': current_bookings})
        else:
            patient_doc['bookings'].append(booking_details)

        # Use the SMTP email sending function
        send_email_to_patient(patient_email, booking_details)

        return f"Success! Your booking has been confirmed. The total cost is ${booking_details['costBreakdown']['totalCost']:.2f} with a patient co-pay of ${booking_details['costBreakdown']['patientCopay']:.2f}. An email has been sent to {patient_email}."

    except Exception as e:
        print(f"An error occurred: {e}")
        return "An internal error occurred during the booking process."


def send_email_to_patient(email: str, booking_details: dict):
    """
    Sends a confirmation email using a secure SMTP connection.
    NOTE: You must configure an application-specific password for your email account
    and store it securely. Do NOT hardcode your main email password here.
    """
    smtp_server = "smtp.gmail.com"  # Example for Gmail
    smtp_port = 587
    sender_email = "niljoshna28@gmail.com"  # Replace with your email address
    password = "nxlcscihekyxcedc"  # Replace with your application-specific password

    if sender_email == "your_email@gmail.com" or password == "your_app_password":
        print("SMTP configuration not set. Cannot send email.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Appointment Confirmation"
    msg["From"] = sender_email
    msg["To"] = email

    # Create the plain-text and HTML versions of your message
    cost_breakdown = booking_details.get('costBreakdown', {})
    total_cost = cost_breakdown.get('totalCost', 0)
    patient_copay = cost_breakdown.get('patientCopay', 0)

    text_content = f"""
    Appointment Confirmation
    Hello,
    Your appointment has been successfully booked with {booking_details.get('doctorName')} on {booking_details.get('appointmentDate')} at {booking_details.get('appointmentTime')}.
    
    Booking Details:
    - Appointment Type: {booking_details.get('bookingType')}
    - Doctor: {booking_details.get('doctorName')}
    - Date: {booking_details.get('appointmentDate')}
    - Time: {booking_details.get('appointmentTime')}
    
    Cost Breakdown:
    - Total Appointment Cost: ${total_cost:.2f}
    - Your Co-pay (After Insurance Claim): ${patient_copay:.2f}
    
    We look forward to seeing you!
    
    Best regards,
    The Clinic Team
    """

    html_content = f"""
    <html>
    <body style="font-family: sans-serif; line-height: 1.6;">
        <h2>Appointment Confirmation</h2>
        <p>Hello,</p>
        <p>Your appointment has been successfully booked with <b>{booking_details.get('doctorName')}</b> on <b>{booking_details.get('appointmentDate')}</b> at <b>{booking_details.get('appointmentTime')}</b>.</p>
        <h3>Booking Details:</h3>
        <ul>
            <li><b>Appointment Type:</b> {booking_details.get('bookingType')}</li>
            <li><b>Doctor:</b> {booking_details.get('doctorName')}</li>
            <li><b>Date:</b> {booking_details.get('appointmentDate')}</li>
            <li><b>Time:</b> {booking_details.get('appointmentTime')}</li>
        </ul>
        <h3>Cost Breakdown:</h3>
        <ul>
            <li><b>Total Appointment Cost:</b> ${total_cost:.2f}</li>
            <li><b>Your Co-pay (After Insurance Claim):</b> ${patient_copay:.2f}</li>
        </ul>
        <p>We look forward to seeing you!</p>
        <p>Best regards,<br>The Clinic Team</p>
    </body>
    </html>
    """

    part1 = MIMEText(text_content, "plain")
    part2 = MIMEText(html_content, "html")
    msg.attach(part1)
    msg.attach(part2)

    try:
        # Create a secure SSL context and a connection
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)  # Secure the connection
            server.login(sender_email, password)
            server.sendmail(sender_email, email, msg.as_string())
            print(f"Email sent successfully via SMTP to {email}")
    except Exception as e:
        print(f"Failed to send email via SMTP: {e}")


# Example Usage:
if __name__ == '__main__':
    # Patient details from the Dialogflow agent
    patient_name = "Tahmina"
    patient_dob = "1992-03-12"
    patient_insurance = "MedStar Health"
    patient_policy = "D123456"

    # Example 1: Successful booking with cost calculation
    print("--- Attempting Successful Booking with Cost Calculation ---")
    new_booking_success = {
        "appointmentDate": "2025-09-03",
        "appointmentTime": "13:00",
        "bookingType": "appointment",
        "doctorName": "Dr. Lucy Morgan, MRCGP",
        "specialty": "General Practitioner",
        "status": "confirmed",
    }
    result_success = validate_and_book(patient_name, patient_dob, patient_insurance, patient_policy, new_booking_success)
    print(result_success)
    print("\n")

    # Example 2: Failed booking (unavailable time slot)
    print("--- Attempting Failed Booking ---")
    new_booking_fail = {
        "appointmentDate": "2025-09-03",
        "appointmentTime": "11:00",
        "bookingType": "appointment",
        "doctorName": "Dr. Adam Collins, MRCGP",
        "specialty": "General Practitioner",
        "status": "confirmed",
    }
    result_fail = validate_and_book(patient_name, patient_dob, patient_insurance, patient_policy, new_booking_fail)
    print(result_fail)
