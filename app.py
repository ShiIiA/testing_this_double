# -*- coding: utf-8 -*-
"""app.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1rRSegdTULlczjwu11IQzYDFAIJmlE8TF
"""

import os
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image
from fairlearn.metrics import demographic_parity_difference, equalized_odds_difference
import shap
from scipy.stats import chi2_contingency
from sklearn.metrics import confusion_matrix, accuracy_score, precision_score, recall_score
from torchvision.models import DenseNet121_Weights
import plotly.express as px
from transformers import pipeline  # For CheXagent model
import re
from collections import Counter

# Set the page configuration (the little snake will appear on the tab)
st.set_page_config(
    page_title="Gender Bias in Radiology",
    page_icon="🐍",  # Displays the snake emoji as the favicon
    layout="wide"
)

# ========== THEME & STYLE FUNCTIONS ==========
def set_background():
    # Fixed light theme with high contrast for clear text visibility.
    style = """
    <style>
    .stApp {
        background: linear-gradient(to bottom right, #ffffff, #e6f7ff);
        color: #000;
    }
    .stTitle {
        font-size: 36px !important;
        font-weight: bold;
        color: #000 !important;
    }
    </style>
    """
    st.markdown(style, unsafe_allow_html=True)

set_background()

def set_gradient_progress_bar():
    st.markdown(
        """
        <style>
        div[data-testid="stProgressBar"] > div[role="progressbar"] > div {
            background: linear-gradient(to right, #4facfe, #00f2fe);
        }
        </style>
        """,
        unsafe_allow_html=True
    )

set_gradient_progress_bar()

# ========== GLOBAL SESSION STATE ==========
if "df" not in st.session_state:
    st.session_state.df = None
if "df_results" not in st.session_state:
    st.session_state.df_results = pd.DataFrame(columns=["Image_ID", "Gender", "Prediction", "Probability"])

# ========== MODEL & HELPER FUNCTIONS ==========
@st.cache_resource(show_spinner=True)
def load_chexnet_model():
    """Loads the pre-trained DenseNet-121 (CheXNet) model."""
    model = models.densenet121(weights=DenseNet121_Weights.IMAGENET1K_V1)
    model.classifier = nn.Linear(1024, 2)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    model.eval()
    return model, device

try:
    chexnet_model, device = load_chexnet_model()
    st.success("✅ CheXNet Model Loaded Successfully!")
except Exception as e:
    st.error(f"🚨 Error loading CheXNet model: {e}")

def unify_gender_label(label):
    text = str(label).strip().lower()
    male_keywords = ["m", "m ", " m", " m ", "male", "man", "masculin"]
    female_keywords = ["f", "f ", " f", " f ", "female", "woman", "femme"]
    if any(kw in text for kw in male_keywords):
        return "M"
    if any(kw in text for kw in female_keywords):
        return "F"
    return "Unknown"

def unify_disease_label(label):
    text = str(label).strip().lower()
    no_disease_keywords = ["no finding", "none", "negative", "normal", "0", "false", "no disease"]
    if any(kw in text for kw in no_disease_keywords):
        return "No Disease"
    return label

@st.cache_resource(show_spinner=True)
def preprocess_image(image):
    transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor()
    ])
    return transform(image).unsqueeze(0)

# ========== STATIC CHATBOT (PREDEFINED ANSWERS) ==========
PREDEFINED_ANSWERS = {
    "what is gender bias?": "Gender bias refers to unequal representation or treatment based on gender. In radiology, it may lead to misdiagnoses if training data is not balanced.",
    "how does gender bias affect radiology?": "Bias can result in inaccurate disease detection and unequal treatment recommendations.",
    "what are common mitigation techniques?": "Techniques include threshold adjustment, reweighing, adversarial debiasing, and post-processing calibration.",
    "which papers are cited?": (
        "Key papers:\n"
        "- [Mehrabi et al. (2021)](https://arxiv.org/abs/1908.09635)\n"
        "- [Obermeyer et al. (2019)](https://www.science.org/doi/10.1126/science.aax2342)\n"
        "- [Larrazabal et al. (2020)](https://www.nature.com/articles/s41467-020-19109-9)"
    ),
    "how can i improve model fairness?": "You can improve fairness by collecting diverse data, applying mitigation techniques, and monitoring performance across subgroups.",
    "default": "I'm sorry, I don't have an answer for that. Please ask another question related to gender bias in radiology."
}

def static_chatbot(user_input):
    user_input = user_input.lower().strip()
    for key in PREDEFINED_ANSWERS:
        if key in user_input:
            return PREDEFINED_ANSWERS[key]
    return PREDEFINED_ANSWERS["default"]

# ========== PAGE FUNCTIONS ==========

def home_page():
    st.title("🏠 Home")
    st.markdown("## Importance of Gender Bias in AI")
    st.markdown(
        """
        **Why Gender Bias Matters:**

        - Ethical concerns: Unfair treatment may result.
        - Clinical impact: Risk of misdiagnosis.
        - Regulatory pressure: Fairness is required.
        - Research evidence: Underrepresentation leads to poorer outcomes.

        This project, designed for WiDS Datathon 2025, explores responsible AI in radiology.
        """
    )
    st.info("Use the sidebar to navigate through the app.")
    st.markdown("---")
    st.markdown("### Thank You")
    st.markdown(
        """
        **Thank you to the mentors, sponsors, and jury of the WiDS Datathon for their invaluable support.**
        """
    )

def upload_data_page():
    st.title("📂 Upload Data")
    uploaded_file = st.file_uploader("Upload your dataset (CSV/XLSX)", type=["csv", "xlsx"], help="Upload a CSV or Excel file containing your data.")
    if uploaded_file:
        try:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith(".csv") else pd.read_excel(uploaded_file)
            # Remove duplicate columns to ensure unique merge keys.
            df = df.loc[:, ~df.columns.duplicated()]
            st.session_state.df = df
            st.write("**Preview of Uploaded Data:**")
            st.dataframe(df.head())
        except Exception as e:
            st.error(f"Error loading file: {e}")
    else:
        st.info("Please upload a dataset to continue.")

def explore_data_page():
    st.title("📊 Explore Data & Prepare")
    df = st.session_state.df
    if df is not None:
        st.subheader("Select Columns")
        gender_col = st.selectbox("🛑 Select Gender Column:", df.columns, help="Column indicating gender.")
        disease_col = st.selectbox("🩺 Select Disease Column:", df.columns, help="Column showing disease status.")
        image_id_col = st.selectbox("🖼️ Select Image ID Column:", df.columns, help="Unique image identifier.")
        # Force the Image ID column to string to avoid type conflicts later.
        df[image_id_col] = df[image_id_col].astype(str)
        df[gender_col] = df[gender_col].apply(unify_gender_label)
        df[disease_col] = df[disease_col].apply(unify_disease_label)
        st.session_state.gender_col = gender_col
        st.session_state.disease_col = disease_col
        st.session_state.image_id_col = image_id_col

        st.subheader("Data Summary")
        st.write(df.describe(include="all"))

        st.markdown("#### Column Distributions")
        for col in df.columns:
            fig, ax = plt.subplots()
            if pd.api.types.is_numeric_dtype(df[col]):
                ax.hist(df[col].dropna(), bins=20, color="#4facfe", edgecolor="black")
                ax.set_title(f"Distribution of {col}")
            else:
                counts = df[col].value_counts()
                ax.bar(counts.index.astype(str), counts.values, color="#00f2fe", edgecolor="black")
                ax.set_title(f"Counts of {col}")
                plt.xticks(rotation=45)
            st.pyplot(fig)
    else:
        st.info("No data uploaded. Please use the Upload Data page.")

def model_prediction_page():
    st.title("🤖 Model Prediction")
    st.markdown("Select an AI model and upload chest X‑ray images for prediction.")
    model_choice = st.selectbox("Select AI Model:", ["CheXNet", "CheXagent"], help="Choose the model to use for prediction.")
    uploaded_images = st.file_uploader("Upload X‑ray Images", type=["png", "jpg", "jpeg"], accept_multiple_files=True, help="Upload one or more images.")
    threshold = st.slider("Decision Threshold", 0.0, 1.0, 0.5, 0.01, help="Adjust the threshold for classifying images as positive for disease.")
    if uploaded_images:
        progress_bar = st.progress(0)
        total_images = len(uploaded_images)
        for i, img in enumerate(uploaded_images, start=1):
            st.write(f"Processing Image {i}/{total_images}")
            st.image(img, caption=f"Uploaded: {img.name}", width=300)
            try:
                image = Image.open(img).convert("RGB")
                tensor_img = preprocess_image(image)
                if model_choice == "CheXNet":
                    tensor_img = tensor_img.to(device)
                    with torch.no_grad():
                        logits = chexnet_model(tensor_img)
                        probs = F.softmax(logits, dim=1)
                        disease_prob = probs[0, 1].item()
                        predicted_label = 1 if disease_prob >= threshold else 0
                elif model_choice == "CheXagent":
                    # Use Hugging Face pipeline for image classification with CheXagent
                    chexagent_pipe = pipeline("image-classification", model="StanfordAIMI/CheXagent-2-3b", trust_remote_code=True)
                    result = chexagent_pipe(image)
                    disease_prob = result[0]["score"]
                    predicted_label = 1 if disease_prob >= threshold else 0
                new_row = {"Image_ID": img.name, "Gender": "Unknown", "Prediction": predicted_label, "Probability": disease_prob}
                st.session_state.df_results = pd.concat([st.session_state.df_results, pd.DataFrame([new_row])], ignore_index=True)
                st.success(f"Prediction: {'Disease Detected' if predicted_label == 1 else 'No Disease'} | Prob: {disease_prob:.2%}")
            except Exception as e:
                st.error(f"Error making prediction: {e}")
            progress_bar.progress(int((i / total_images) * 100))
    else:
        st.info("Upload images to generate predictions.")

def gender_bias_analysis_page():
    st.title("⚖️ Gender Bias Analysis")
    df = st.session_state.df
    df_results = st.session_state.df_results
    if df is None or df_results.empty:
        st.info("No prediction data available yet.")
    else:
        gender_col = st.session_state.get("gender_col", None)
        disease_col = st.session_state.get("disease_col", None)
        image_id_col = st.session_state.get("image_id_col", None)
        if gender_col and disease_col and image_id_col:
            # Merge prediction results with original data using the unique image identifier.
            if "Unknown" in df_results["Gender"].values:
                df_merged = pd.merge(df_results, df[[image_id_col, gender_col]], how="left", left_on="Image_ID", right_on=image_id_col)
                df_merged["Gender"] = df_merged[gender_col].fillna("Unknown")
                st.session_state.df_results = df_merged[["Image_ID", "Gender", "Prediction", "Probability"]]
                df_results = st.session_state.df_results
        total_F = df_results[df_results["Gender"] == "F"].shape[0]
        total_M = df_results[df_results["Gender"] == "M"].shape[0]
        F_disease = df_results[(df_results["Gender"] == "F") & (df_results["Prediction"] == 1)].shape[0]
        M_disease = df_results[(df_results["Gender"] == "M") & (df_results["Prediction"] == 1)].shape[0]
        rate_F = F_disease / total_F if total_F > 0 else 0
        rate_M = M_disease / total_M if total_M > 0 else 0
        st.write(f"**Female Detection Rate:** {rate_F:.2%} (F: {total_F} images)")
        st.write(f"**Male Detection Rate:** {rate_M:.2%} (M: {total_M} images)")
        bias_diff = abs(rate_F - rate_M)
        st.write(f"**Bias Difference (F vs. M):** {bias_diff:.4f}")
        if bias_diff > 0.1:
            st.warning("Significant bias detected. Consider mitigation steps.")
        else:
            st.success("Bias difference is within acceptable limits.")

def bias_mitigation_simulation_page():
    st.title("🛠️ Bias Mitigation & Simulation")
    st.markdown("### Advanced Fairness Analysis")
    df = st.session_state.df
    df_results = st.session_state.df_results
    if df is None or df_results.empty:
        st.info("No prediction data available yet.")
    else:
        advanced = st.checkbox("Use advanced fairness approach", help="Enable advanced fairness metrics computation.")
        if advanced:
            df_merged = pd.merge(df, df_results, how="inner", left_on=st.session_state.get("image_id_col", "Image_ID"), right_on="Image_ID")
            target_col = st.selectbox("Select Ground-Truth Disease Column (Advanced):", df.columns.tolist(), help="Choose the column with true disease labels.")
            sensitive_col = st.selectbox("Select Sensitive Attribute (Advanced):", df.columns.tolist(), help="Choose the sensitive attribute (e.g., Gender).")
            if target_col and sensitive_col:
                try:
                    y_true = df_merged[target_col]
                    y_pred = df_merged["Prediction"]
                    sensitive = df_merged[sensitive_col]
                    dp_diff = demographic_parity_difference(y_true, y_pred, sensitive_features=sensitive)
                    eo_diff = equalized_odds_difference(y_true, y_pred, sensitive_features=sensitive)
                    st.write(f"**Demographic Parity Difference:** {dp_diff:.4f}")
                    st.write(f"**Equalized Odds Difference:** {eo_diff:.4f}")
                    acc = accuracy_score(y_true, y_pred)
                    prec = precision_score(y_true, y_pred, zero_division=0)
                    rec = recall_score(y_true, y_pred, zero_division=0)
                    st.write(f"**Accuracy:** {acc:.2%}")
                    st.write(f"**Precision:** {prec:.2%}")
                    st.write(f"**Recall:** {rec:.2%}")
                    cm = confusion_matrix(y_true, y_pred)
                    fig_cm, ax_cm = plt.subplots()
                    cax = ax_cm.matshow(cm, cmap=plt.cm.Blues)
                    fig_cm.colorbar(cax)
                    for (i, j), val in np.ndenumerate(cm):
                        ax_cm.text(j, i, f'{val}', va='center', ha='center')
                    ax_cm.set_xticks(np.arange(2))
                    ax_cm.set_yticks(np.arange(2))
                    ax_cm.set_xticklabels(["No Disease", "Disease"])
                    ax_cm.set_yticklabels(["No Disease", "Disease"])
                    ax_cm.set_xlabel("Predicted")
                    ax_cm.set_ylabel("True")
                    ax_cm.set_title("Confusion Matrix")
                    st.pyplot(fig_cm)
                except Exception as e:
                    st.error(f"Error computing metrics: {e}")
        st.markdown("---")
        st.markdown("### Mitigation Approaches")
        st.markdown("**1. Resampling/Upweighting  |  2. Threshold Adjustment  |  3. Reweighing  |  4. Adversarial Debiasing  |  5. Post-Processing Calibration**")
        st.success("Mitigation recommendations complete!")

def gender_bias_testing_page():
    st.title("🧪 Gender Bias Testing")
    st.markdown("### Test Bias Mitigation via Threshold Adjustment")
    df_results = st.session_state.df_results
    if df_results.empty:
        st.info("No prediction data available. Generate predictions first.")
    else:
        thresh_F = st.slider("Threshold for Female", 0.0, 1.0, 0.5, 0.01, help="Threshold for female predictions.")
        thresh_M = st.slider("Threshold for Male", 0.0, 1.0, 0.5, 0.01, help="Threshold for male predictions.")
        df_new = df_results.copy()
        def adjust_pred(row):
            if row["Gender"] == "M":
                return 1 if row["Probability"] >= thresh_M else 0
            elif row["Gender"] == "F":
                return 1 if row["Probability"] >= thresh_F else 0
            else:
                return row["Prediction"]
        df_new["Adjusted_Prediction"] = df_new.apply(adjust_pred, axis=1)
        total_F = df_new[df_new["Gender"]=="F"].shape[0]
        total_M = df_new[df_new["Gender"]=="M"].shape[0]
        F_disease = df_new[(df_new["Gender"]=="F") & (df_new["Adjusted_Prediction"]==1)].shape[0]
        M_disease = df_new[(df_new["Gender"]=="M") & (df_new["Adjusted_Prediction"]==1)].shape[0]
        rate_F = F_disease / total_F if total_F > 0 else 0
        rate_M = M_disease / total_M if total_M > 0 else 0
        st.write(f"**Adjusted Female Detection Rate:** {rate_F:.2%} (F: {total_F} images)")
        st.write(f"**Adjusted Male Detection Rate:** {rate_M:.2%} (M: {total_M} images)")
        diff = abs(rate_F - rate_M)
        st.write(f"**Bias Difference (F vs. M):** {diff:.4f}")
        if diff > 0.1:
            st.warning("Significant bias remains.")
        else:
            st.success("Bias difference acceptable after adjustment.")
        st.markdown("#### Adjusted Predictions Preview")
        st.dataframe(df_new.head())

def explainable_analysis_page():
    st.title("🔍 Explainable Analysis")
    st.markdown("This page analyzes textual features related to false predictions to help understand potential bias.")
    df = st.session_state.df
    df_results = st.session_state.df_results
    if df is None or df_results.empty:
        st.info("No prediction data available.")
        return
    disease_col = st.session_state.get("disease_col", None)
    image_id_col = st.session_state.get("image_id_col", None)
    if disease_col is None or image_id_col is None:
        st.info("Required column selections are missing.")
        return
    merged = pd.merge(df_results, df[[image_id_col, disease_col]], how="left", left_on="Image_ID", right_on=image_id_col)
    merged = merged.rename(columns={disease_col: "True_Label"})
    merged["Correct"] = merged.apply(lambda row: (row["Prediction"] == 1 and row["True_Label"] != "No Disease") or (row["Prediction"] == 0 and row["True_Label"] == "No Disease"), axis=1)
    st.write("Merged Predictions with Ground Truth:")
    st.dataframe(merged.head())
    symptom_col = None
    for col in df.columns:
        if "symptom" in col.lower():
            symptom_col = col
            break
    if symptom_col is None:
        st.info("No 'Symptoms' column found for textual analysis.")
        return
    st.markdown("### Textual Analysis of Symptoms in False Predictions")
    false_preds = merged[merged["Correct"] == False]
    st.write(f"Number of false predictions: {false_preds.shape[0]}")
    if false_preds.empty:
        st.info("No false predictions to analyze.")
        return
    text_data = " ".join(false_preds[symptom_col].dropna().astype(str).tolist())
    words = re.findall(r'\w+', text_data.lower())
    word_counts = Counter(words)
    common_words = word_counts.most_common(20)
    st.markdown("#### Most Common Words in Symptoms (False Predictions)")
    st.table(common_words)
    try:
        from wordcloud import WordCloud
        wordcloud = WordCloud(width=800, height=400, background_color="white").generate(text_data)
        plt.figure(figsize=(10,5))
        plt.imshow(wordcloud, interpolation="bilinear")
        plt.axis("off")
        st.pyplot(plt)
    except Exception as e:
        st.info("WordCloud could not be generated.")

def importance_gender_bias_page():
    st.title("📚 The Importance of Gender Bias")
    st.markdown(
        """
        **Addressing Gender Bias is Critical:**

        - Ethical Imperative: Fair treatment is a moral obligation.
        - Clinical Impact: Biased models risk misdiagnosis.
        - Regulatory Requirements: Fairness is essential.
        - Research Evidence: Underrepresentation leads to poorer outcomes.
        """
    )
    st.markdown("### References")
    st.markdown(
        """
        - [Mehrabi et al. (2021)](https://arxiv.org/abs/1908.09635)
        - [Obermeyer et al. (2019)](https://www.science.org/doi/10.1126/science.aax2342)
        - [Larrazabal et al. (2020)](https://www.nature.com/articles/s41467-020-19109-9)
        """
    )

def about_chexnet_model_page():
    st.title("🧠 About CheXNet Model")
    st.markdown(
        """
        **CheXNet** is based on **DenseNet-121**.

        - Dataset: ChestX-ray14
        - Architecture: Convolutional Neural Network (CNN)
        - Performance: Comparable to radiologists for pneumonia detection.
        - Paper: [CheXNet: Radiologist-Level Pneumonia Detection](https://arxiv.org/abs/1711.05225)
        """
    )

def about_chexagent_page():
    st.title("🧠 About CheXagent")
    st.markdown(
        """
        **CheXagent** is a chest X‑ray analysis model provided by Stanford AIMI on Hugging Face.

        - Model: StanfordAIMI/CheXagent-2-3b
        - Description: A lightweight model for rapid chest X‑ray analysis.
        - Integration: Loaded via the Transformers library using an image-classification pipeline.
        - Repository: [CheXagent on Hugging Face](https://huggingface.co/StanfordAIMI/CheXagent-2-3b)
        """
    )

def meet_the_team_page():
    st.title("👥 Meet the Team")
    team_members = [
        {"name": "Yuying", "role": "Data Scientist"},
        {"name": "Siwen", "role": "ML Engineer"},
        {"name": "Zhi", "role": "Research Analyst"},
        {"name": "Maude", "role": "UX Designer"}
    ]
    cols = st.columns(len(team_members))
    for i, member in enumerate(team_members):
        with cols[i]:
            st.image("https://via.placeholder.com/150", width=150)
            st.write(f"**{member['name']}**")
            st.write(f"*{member['role']}*")

def chatbot_page():
    st.title("💬 Chatbot")
    st.markdown("Ask questions about gender bias in radiology. This chatbot provides predefined answers.")
    if "chat_history" not in st.session_state:
         st.session_state.chat_history = []
    with st.form("chat_form", clear_on_submit=True):
         user_message = st.text_input("Your question:", key="chat_message", help="e.g., 'What is gender bias?'")
         submitted = st.form_submit_button("Send")
         if submitted and user_message:
             # Use the static chatbot to respond.
             response = static_chatbot(user_message)
             st.session_state.chat_history.append(("You", user_message))
             st.session_state.chat_history.append(("Chatbot", response))
    st.markdown("### Conversation")
    for speaker, message in st.session_state.chat_history:
         st.markdown(f"**{speaker}:** {message}")

def posters_page():
    st.title("🖼️ Posters")
    st.markdown("Below are our project posters:")
    poster_files = ["1.png", "2.png", "3.png", "4.png", "5.png"]
    cols = st.columns(3)
    for i, poster in enumerate(poster_files):
        with cols[i % 3]:
            try:
                st.image(poster, caption=f"Poster {i+1}", use_column_width=True)
            except Exception as e:
                st.error(f"Error loading poster {poster}: {e}")

def datathon_resources_page():
    st.title("📖 Datathon Resources")
    st.markdown(
        """
        **WiDS Datathon 2025 Resources**

        - **Theme:** Harnessing AI Safely: Addressing the Challenges of Autonomous Systems
        - **Guidelines & Schedule:** [Datathon Guidelines & Schedule](https://em-lyon.com/en/women-in-data-science)
        - **Event Website:** [WiDS Datathon 2025](https://em-lyon.com/en/women-in-data-science)
        - **Sponsor & Contact Information:** Refer to the event materials provided by emlyon and partners.
        """
    )

def project_overview_page():
    st.title("📈 Project Overview")
    st.markdown(
        """
        **Project Objective:**

        To explore and mitigate gender bias in AI-driven chest X‑ray analysis while promoting responsible AI practices.

        **Impact:**

        - Enhance fairness in radiological predictions.
        - Promote ethical and safe AI development.
        - Contribute actionable mitigation strategies aligned with the datathon theme.

        **Approach:**

        - Comprehensive data exploration and visualization.
        - Comparison of multiple AI models.
        - Bias analysis and explainable analysis.
        """
    )

def feedback_page():
    st.title("📝 Feedback")
    st.markdown("We value your input! Please share your thoughts and suggestions below:")
    feedback = st.text_area("Your Feedback", help="Enter your comments here...")
    if st.button("Submit Feedback"):
        st.success("Thank you for your feedback!")
        # Optionally, save feedback to a file or database.

def interactive_demos_page():
    st.title("🔍 Interactive Demonstrations")
    st.markdown("Compare predictions from CheXNet and CheXagent side by side.")
    uploaded_image = st.file_uploader("Upload a single X‑ray image", type=["png", "jpg", "jpeg"])
    threshold = st.slider("Decision Threshold", 0.0, 1.0, 0.5, 0.01)
    if uploaded_image:
        image = Image.open(uploaded_image).convert("RGB")
        st.image(image, caption="Uploaded X‑ray", width=300)
        tensor_img = preprocess_image(image)
        # CheXNet prediction
        tensor_img_chexnet = tensor_img.to(device)
        with torch.no_grad():
            logits = chexnet_model(tensor_img_chexnet)
            probs = F.softmax(logits, dim=1)
            chexnet_prob = probs[0, 1].item()
            chexnet_pred = 1 if chexnet_prob >= threshold else 0
        # CheXagent prediction
        chexagent_pipe = pipeline("image-classification", model="StanfordAIMI/CheXagent-2-3b", trust_remote_code=True)
        result = chexagent_pipe(image)
        chexagent_prob = result[0]["score"]
        chexagent_pred = 1 if chexagent_prob >= threshold else 0
        st.markdown("### Predictions Comparison")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**CheXNet**")
            st.write(f"Prediction: {'Disease' if chexnet_pred == 1 else 'No Disease'}")
            st.write(f"Probability: {chexnet_prob:.2%}")
        with col2:
            st.markdown("**CheXagent**")
            st.write(f"Prediction: {'Disease' if chexagent_pred == 1 else 'No Disease'}")
            st.write(f"Probability: {chexagent_prob:.2%}")
    else:
        st.info("Upload an image for comparison.")

def live_metrics_dashboard_page():
    st.title("📊 Live Metrics Dashboard")
    st.markdown("This dashboard displays key metrics from current predictions.")
    df_results = st.session_state.df_results
    if df_results.empty:
        st.info("No prediction data available yet.")
    else:
        total = df_results.shape[0]
        st.write(f"**Total Predictions:** {total}")
        pred_counts = df_results["Prediction"].value_counts()
        st.write("**Prediction Distribution:**")
        st.dataframe(pred_counts)
        if "Gender" in df_results.columns:
            total_F = df_results[df_results["Gender"]=="F"].shape[0]
            total_M = df_results[df_results["Gender"]=="M"].shape[0]
            F_disease = df_results[(df_results["Gender"]=="F") & (df_results["Prediction"]==1)].shape[0]
            M_disease = df_results[(df_results["Gender"]=="M") & (df_results["Prediction"]==1)].shape[0]
            rate_F = F_disease/total_F if total_F > 0 else 0
            rate_M = M_disease/total_M if total_M > 0 else 0
            st.write(f"**Female Detection Rate:** {rate_F:.2%} ({total_F} images)")
            st.write(f"**Male Detection Rate:** {rate_M:.2%} ({total_M} images)")
            st.write(f"**Bias Difference:** {abs(rate_F - rate_M):.4f}")
        chart_data = df_results[["Prediction", "Probability"]]
        st.line_chart(chart_data)

# ========== SIDEBAR NAVIGATION ==========
page_options = [
    "🏠 Home",
    "📂 Upload Data",
    "📊 Explore Data & Prepare",
    "🤖 Model Prediction",
    "⚖️ Gender Bias Analysis",
    "🛠️ Bias Mitigation & Simulation",
    "🧪 Gender Bias Testing",
    "🔍 Explainable Analysis",
    "💬 Chatbot",
    "🖼️ Posters",
    "📚 The Importance of Gender Bias",
    "🧠 About CheXNet Model",
    "🧠 About CheXagent",
    "👥 Meet the Team",
    "📖 Datathon Resources",
    "📈 Project Overview",
    "📝 Feedback",
    "🔍 Interactive Demonstrations",
    "📊 Live Metrics Dashboard"
]

selected_page = st.sidebar.radio("Navigate to", page_options, help="Select a section to explore.")

# ========== PAGE RENDERING ==========
if selected_page == "🏠 Home":
    home_page()
elif selected_page == "📂 Upload Data":
    upload_data_page()
elif selected_page == "📊 Explore Data & Prepare":
    explore_data_page()
elif selected_page == "🤖 Model Prediction":
    model_prediction_page()
elif selected_page == "⚖️ Gender Bias Analysis":
    gender_bias_analysis_page()
elif selected_page == "🛠️ Bias Mitigation & Simulation":
    bias_mitigation_simulation_page()
elif selected_page == "🧪 Gender Bias Testing":
    gender_bias_testing_page()
elif selected_page == "🔍 Explainable Analysis":
    explainable_analysis_page()
elif selected_page == "💬 Chatbot":
    chatbot_page()
elif selected_page == "🖼️ Posters":
    posters_page()
elif selected_page == "📚 The Importance of Gender Bias":
    importance_gender_bias_page()
elif selected_page == "🧠 About CheXNet Model":
    about_chexnet_model_page()
elif selected_page == "🧠 About CheXagent":
    about_chexagent_page()
elif selected_page == "👥 Meet the Team":
    meet_the_team_page()
elif selected_page == "📖 Datathon Resources":
    datathon_resources_page()
elif selected_page == "📈 Project Overview":
    project_overview_page()
elif selected_page == "📝 Feedback":
    feedback_page()
elif selected_page == "🔍 Interactive Demonstrations":
    interactive_demos_page()
elif selected_page == "📊 Live Metrics Dashboard":
    live_metrics_dashboard_page()