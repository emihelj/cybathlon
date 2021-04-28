from collections import Counter

import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.svm import SVC
from tensorflow.keras.wrappers.scikit_learn import KerasClassifier

from .feature_extraction_functions.csp import CSP
from .feature_extraction_functions.fbcsp import FBCSP
from .feature_extraction_functions.riemann import Riemann
from .feature_extraction_functions.convnets import ShallowConvNet
from .preprocessing import cropping, preprocessing

# Reproducibility
seed_value = 0
np.random.seed(seed_value)


def get_model(params):
    model_name = params['model_name']
    if model_name == 'CSP':
        model, search_space = get_CSP_model()
    elif model_name == 'FBCSP':
        model, search_space = get_FBCSP_model()
    elif model_name == 'Riemann':
        model, search_space = get_Riemann_model()
    elif model_name == 'ConvNet':
        model, search_space = get_ConvNet_model()
    else:
        raise RuntimeError('Invalid model selection')
    return model, search_space


def get_CSP_model():
    search_space = {'classifier__C': (1e-3, 1e3, 'log-uniform')}
    model = Pipeline(steps=[('feat', CSP()),
                            ('classifier', SVC())])
    return model, search_space


def get_FBCSP_model():
    search_space = {'feat__regularize_cov': [False, True],
                    'feat__f_order': (2, 5),
                    'classifier__C': (1e-2, 1e3, 'log-uniform'), }
    model = Pipeline([('feat', FBCSP(fs=500, f_type='butter', f_order=2,
                                     m=2, k=-1, regularize_cov=False)),
                      ('classifier', SVC(kernel='rbf', gamma='scale', C=10))])
    return model, search_space


def get_Riemann_model():
    search_space = {'feat__f_order': (1, 5),
                    'feat__f_type': ['butter', 'cheby', 'ellip'],
                    'classifier__kernel': ['rbf', 'linear', 'poly'],
                    'classifier__degree': (1, 5),
                    'classifier__C': (1e-1, 1e3, 'log-uniform')}
    model = Pipeline([('feat', Riemann(fs=500)),
                      ('classifier', SVC(kernel='linear', gamma='scale',
                                         C=10))])
    return model, search_space


def get_ConvNet_model():
    search_space = {}
    model = KerasClassifier(ShallowConvNet, epochs=500,
                            batch_size=16, lr=1e-3)
    return model, search_space


def predict(X, models, is_convnet, n_crops=10, crop_len=0.5, fs=500, should_reref=True, should_filter=False, should_standardize=True):
    """Return prediction of a trained model given input EEG data.

    Arguments:htop
        X {np.array} -- EEG array of shape (n_trials, n_channels, n_samples)
        models {List[object]} -- Trained model
        is_convnet {bool} -- Model is convNet

    Returns:
        np.array -- Array of predictions
    """
    if not isinstance(models, list):
        models = [models]

    # Cropping
    X, _ = cropping(X, [None], fs, n_crops, crop_len)

    if is_convnet:
        # Preprocess
        X = preprocessing(X, fs, should_reref,
                          should_filter, should_standardize)

        # ConvNet case - Adapt input shape & convert probabilities to int
        y_prob = [model.predict(X[:, :, :, np.newaxis])
                  for model in models]
        y_probs = np.sum(y_prob, axis=0)
        y_preds = np.argmax(y_probs, axis=1)
    else:
        y_preds = np.concatenate([model.predict(X) for model in models])

    y_pred = Counter(y_preds).most_common()[0][0]
    return y_pred
