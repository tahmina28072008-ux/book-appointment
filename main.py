import logging
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify
from google.cloud import firestore

# Initialize the Flask app and Firestore DB
app = Flask(__name__)
db = firestore.Client()
logging.basicConfig(level=logging.DEBUG)

# Mocked data for demonstration
DOCTORS_DATA = {
    'car-001': {'name': 'Dr. Elizabeth Turner, FRCP', 'specialty': 'Cardiologist', 'location': 'London'},
    'ped-002': {'name': 'Dr. Marcus Riley', 'specialty': 'Pediatrician', 'location': 'London'},
    'der-003': {'name': 'Dr. Sarah Chen', 'specialty': 'Dermatologist', 'location': 'London'},
}

# In a real app, this would be a secure config.
SMTP_CONFIG = {
    'server': 'smtp.gmail.com', # Example for Gmail
    'port': 587,
    'email': 'niljoshna28@gmail.com',
    'password': 'nxlc scih ekyx cedc'
}

# --- HELPER FUNCTIONS ---

def get_fulfillment_response(message):
    """Creates a standard Dialogflow fulfillment response."""
    return {
        'fulfillment_response': {
            'messages': [
                {'text': {'text': [message]}}
            ]
        }
    }

def send_confirmation_email(patient_info, appointment_details):
    """Sends a confirmation email to the patient."""
    try:
        sender_email = SMTP_CONFIG['email']
        receiver_email = patient_info['email']
        password = SMTP_CONFIG['password']

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "MedStar Health Appointment Confirmation"
        msg["From"] = sender_email
        msg["To"] = receiver_email

        html = f"""
        <html>
        <body>
            <p>Hello {patient_info['name']},</p>
            <p>Your appointment has been successfully booked with <b>{appointment_details['doctor']}</b> for <b>{appointment_details['date']}</b> at <b>{appointment_details['time']}</b>.</p>
            <p><b>Appointment Details:</b></p>
            <ul>
                <li>Doctor: {appointment_details['doctor']}</li>
                <li>Specialty: {appointment_details['specialty']}</li>
                <li>Date: {appointment_details['date']}</li>
                <li>Time: {appointment_details['time']}</li>
                <li>Location: {appointment_details['location']}</li>
            </ul>
            <p>We look forward to seeing you!</p>
        </body>
        </html>
        """
        part1 = MIMEText(html, "html")
        msg.attach(part1)

        with smtplib.SMTP(SMTP_CONFIG['server'], SMTP_CONFIG['port']) as server:
            server.starttls()
            server.login(sender_email, password)
            server.sendmail(sender_email, receiver_email, msg.as_string())
        
        logging.info("Confirmation email sent successfully.")
        return True
    except Exception as e:
        logging.error(f"Failed to send email: {e}")
        return False

# --- WEBHOOK HANDLERS ---

def handle_check_availability(params):
    """
    Handles the `CheckAvailability` tag. Finds and lists doctors based on
    date or specialty.
    """
    specialty = params.get('specialty')
    date_param = params.get('date')
    
    # Mock data for demonstration
    available_doctors = []
    
    if specialty:
        for doctor_id, info in DOCTORS_DATA.items():
            if info['specialty'].lower() == specialty.lower():
                available_doctors.append(info)
        
        if not available_doctors:
            return get_fulfillment_response(f"I'm sorry, we don't have any {specialty} doctors available. Is there another specialty you would like to book with?")
    
    else: # If no specialty, just return all doctors for now
        available_doctors = list(DOCTORS_DATA.values())

    # Build the response message
    response_message = "Here are the doctors available for you:\n"
    for i, doc in enumerate(available_doctors):
        response_message += f"\n{i+1}. {doc['name']} ({doc['specialty']}) - Next available date: {datetime.date.today() + datetime.timedelta(days=i+1)}"
        
    response_message += "\n\nPlease tell me which doctor or date you would like to book."
    
    return get_fulfillment_response(response_message)

def handle_start_booking(params):
    """
    This function is called after the user selects a doctor.
    It prepares the conversation for collecting patient details.
    """
    doctor_name = params.get('doctorName')
    
    if doctor_name:
        return get_fulfillment_response(f"Great, you've selected {doctor_name}. To book your appointment, I'll need some details. What is your full name?")
    else:
        return get_fulfillment_response("Please select a doctor first to proceed.")
        
def handle_confirm_cost(params):
    """
    Calculates and returns the estimated cost and insurance details.
    """
    # This is a very simple cost calculation for demonstration
    total_cost = 150
    insurance_coverage = 120
    out_of_pocket = total_cost - insurance_coverage
    
    response_message = f"The estimated total cost for this visit is £{total_cost}. Your insurance with {params.get('insuranceProvider')} is expected to cover £{insurance_coverage}, leaving an out-of-pocket expense of £{out_of_pocket}. Do you wish to proceed?"
    
    return get_fulfillment_response(response_message)
    
def handle_final_booking(params):
    """
    Handles the `FinalBooking` tag. Saves all data to Firestore and sends email.
    """
    try:
        # Extract and format the patient info from the parameters
        patient_info = {
            "date": params.get('date'),
            "doctor": params.get('doctorName'),
            "location": DOCTORS_DATA.get(params.get('doctorId'), {}).get('location'),
            "specialty": DOCTORS_DATA.get(params.get('doctorId'), {}).get('specialty'),
            "status": "confirmed",
            "time": params.get('time'),
            "city": "London",
            "createdAt": firestore.SERVER_TIMESTAMP,
            "dateOfBirth": params.get('dateOfBirth'),
            "email": params.get('email'), # Assuming an email is also collected
            "insuranceProvider": params.get('insuranceProvider'),
            "name": params.get('name'),
            "phoneNumber": params.get('phoneNumber'), # Assuming phone number is also collected
            "policyNumber": params.get('policyNumber'),
            "surname": params.get('surname'),
            "updatedAt": firestore.SERVER_TIMESTAMP
        }
        
        # Save to Firestore 'patients' collection
        doc_ref = db.collection('patients').add(patient_info)
        logging.info(f"Patient data and appointment saved to Firestore with ID: {doc_ref[1].id}")
        
        # Send confirmation email
        email_sent = send_confirmation_email(patient_info, patient_info) # Using patient_info for both
        
        if email_sent:
            return get_fulfillment_response("Your appointment has been successfully booked. A confirmation email has been sent to you. We look forward to seeing you soon.")
        else:
            return get_fulfillment_response("Your appointment has been booked, but we were unable to send a confirmation email. Please check your details or contact us directly.")

    except Exception as e:
        logging.error(f"Error booking final appointment: {e}", exc_info=True)
        return get_fulfillment_response("I'm sorry, I couldn't book your appointment at this time. Please try again.")

# --- MAIN WEBHOOK ROUTER ---

@app.route('/', methods=['POST'])
async def dialogflow_webhook():
    """Routes incoming Dialogflow requests to the correct handler."""
    try:
        req = await request.json
        tag = req.get('fulfillmentInfo', {}).get('tag')
        params = req.get('sessionInfo', {}).get('parameters', {})
        
        logging.info(f"Received request for tag: {tag} with params: {params}")

        if tag == 'CheckAvailability':
            return jsonify(handle_check_availability(params))
        elif tag == 'StartBooking':
            return jsonify(handle_start_booking(params))
        elif tag == 'ConfirmCost':
            return jsonify(handle_confirm_cost(params))
        elif tag == 'FinalBooking':
            return jsonify(handle_final_booking(params))
        else:
            return jsonify(get_fulfillment_response("I'm not sure how to handle that request. Can you please rephrase?"))
            
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}", exc_info=True)
        return jsonify(get_fulfillment_response("An unexpected error occurred. Please try again later."))

if __name__ == '__main__':
    app.run(debug=True, port=8080)
