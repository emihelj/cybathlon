import logging
from collections import Counter

import numpy as np
import mne
from sklearn.metrics import balanced_accuracy_score, cohen_kappa_score
from bokeh.io import curdoc
from bokeh.plotting import figure
from bokeh.models import Div, Select, Button, Slider
from bokeh.models import ColumnDataSource, CheckboxButtonGroup
from bokeh.layouts import row, column

from config import main_config, test_config
from src.vhdr_formatter import load_vhdr
from src.models import predict
from src.pipeline import load_pipeline


class TestWidget:
    def __init__(self):
        self.data_path = main_config['data_path']
        self.markers_decoding = test_config['markers_decoding']
        self.pred_decoding = main_config['pred_decoding']
        self.decoding2pred = {v: k for k, v in self.pred_decoding.items()}
        self._data = {}
        self.chrono_source = ColumnDataSource(dict(ts=[], y_true=[],
                                                   y_pred=[]))

        # Model
        self.models_path = main_config['models_path']
        self.n_crops = test_config['n_crops']
        self.crop_len = test_config['crop_len']

    @property
    def available_pilots(self):
        pilots = self.data_path.glob('*')
        return [''] + [p.parts[-1] for p in pilots]

    @property
    def selected_pilot(self):
        return self.select_pilot.value

    @property
    def available_sessions(self):
        if self.select_pilot != '':
            pilot_path = self.data_path / self.selected_pilot
            sessions = pilot_path.glob('*')
            return [''] + [s.name for s in sessions]

    @property
    def session_path(self):
        if self.selected_pilot != '':
            return self.data_path / self.selected_pilot / self.select_session.value

    @property
    def available_runs(self):
        if self.select_session.value != '':
            runs = self.session_path.glob('game/*.vhdr')
            return [''] + [r.name for r in runs]

    @property
    def run_path(self):
        return self.session_path / 'game' / self.select_run.value

    @property
    def selected_preproc(self):
        active = self.checkbox_preproc.active
        return [self.checkbox_preproc.labels[i] for i in active]

    @property
    def should_reref(self):
        return 'Rereference' in self.selected_preproc

    @property
    def should_filter(self):
        return 'Filter' in self.selected_preproc

    @property
    def should_standardize(self):
        return 'Standardize' in self.selected_preproc

    @property
    def selected_settings(self):
        active = self.checkbox_settings.active
        return [self.checkbox_settings.labels[i] for i in active]

    @property
    def available_models(self):
        ml_models = [p.name for p in self.models_path.glob('*.pkl')]
        dl_models = [p.name for p in self.models_path.glob('*.h5')]
        return [''] + ml_models + dl_models

    @property
    def model_path(self):
        return self.models_path / self.select_model.value

    @property
    def is_convnet(self):
        return self.select_model.value.split('.')[-1] == 'h5'

    @property
    def win_len(self):
        return int(self.fs*self.slider_win_len.value)

    @property
    def accuracy(self):
        y_pred = self.chrono_source.data['y_pred']
        y_true = self.chrono_source.data['y_true']
        return balanced_accuracy_score(y_true, y_pred)

    @property
    def kappa(self):
        y_pred = self.chrono_source.data['y_pred']
        y_true = self.chrono_source.data['y_true']
        return cohen_kappa_score(y_true, y_pred)

    def on_pilot_change(self, attr, old, new):
        logging.info(f'Select pilot {new}')
        self.select_session.value = ''
        self.select_run.value = ''
        self.update_widget()

    def on_session_change(self, attr, old, new):
        logging.info(f'Select session {new}')
        self.select_run.value = ''
        self.update_widget()

    def on_run_change(self, attr, old, new):
        if new == '':
            return

        logging.info(f'Select run {new}')
        self.update_widget()

        raw = load_vhdr(self.run_path,
                        resample=False,
                        preprocess=test_config['prefilt'],
                        remove_ch=['Fp1', 'Fp2'])
        self.fs = raw.info['sfreq']

        # Get channels
        available_channels = raw.ch_names
        self.channel2idx = {c: i + 1 for i, c in enumerate(available_channels)}

        # Get events & decode
        events = mne.events_from_annotations(raw, verbose=False)[0]
        decoded_events = []

        for ts, _, marker in events:
            raw_label = str(marker)
            if len(raw_label) == 2:
                label = int(raw_label[0])
                if label in self.markers_decoding.keys():
                    decoded_events.append([ts, label])

        # Store signal and events
        self._data['values'], self._data['ts'] = raw.get_data(
            return_times=True)
        self._data['events'] = [(ts/self.fs, action)
                                for ts, action in decoded_events]

        counter = Counter([e[-1] for e in decoded_events])
        logging.info(counter)
        self.div_info.text = f'<b>Frequency</b>: {self.fs} Hz<br>'
        for label, count in counter.items():
            self.div_info.text += f'<b>{label}</b>: {count} <br>'

    def on_model_change(self, attr, old, new):
        logging.info(f'Select model {new}')
        self.select_model.options = self.available_models
        if new != '':
            if 'Ensemble' in self.selected_settings:
                self.models_name = [f'model{i}.h5'
                                    for i in [0, 1, 2]]
                logging.info(f'Loading ensemble of models {self.models_name}')
                self.pipeline = [load_pipeline(self.models_path / name)
                                 for name in self.models_name]
            else:
                self.pipeline = load_pipeline(self.model_path)

    def update_widget(self):
        self.select_session.options = self.available_sessions
        self.select_run.options = self.available_runs
        self.select_model.options = self.available_models
        self.button_validate.label = 'Validate'
        self.button_validate.button_type = 'primary'
        self.chrono_source.data = dict(ts=[], y_true=[], y_pred=[])

    def on_validate_start(self):
        assert self.select_run.value != '', 'Select a run first !'
        assert self.select_model.value != '', 'Select a model first !'
        self.update_widget()
        self.button_validate.label = 'Validating...'
        self.button_validate.button_type = 'warning'
        curdoc().add_next_tick_callback(self.on_validate)

    def on_validate(self):
        for ts, groundtruth in self._data['events']:
            # Extract epoch data
            end_idx = np.argmin(abs(ts - self._data['ts']))
            start_idx = end_idx - self.win_len
            epoch = self._data['values'][np.newaxis, :, start_idx:end_idx]

            # Predict
            y_pred = predict(epoch, self.pipeline, self.is_convnet,
                             self.n_crops, self.crop_len, self.fs,
                             self.should_reref, self.should_filter,
                             self.should_standardize)

            y_true = self.decoding2pred[self.markers_decoding[groundtruth]]
            self.chrono_source.stream(dict(ts=[ts],
                                           y_true=[y_true],
                                           y_pred=[y_pred]))

        # Printing metrics
        self.div_info.text += f'<b>Accuracy:</b> {self.accuracy:.2f} <br>'
        self.div_info.text += f'<b>Kappa:</b> {self.kappa:.2f} <br>'
        logging.info(f'Accuracy: {self.accuracy:.2f} - '
                     f'Kappa: {self.kappa:.2f}')

        self.button_validate.label = 'Finished'
        self.button_validate.button_type = 'success'

    def create_widget(self):
        # Select - Pilot
        self.select_pilot = Select(title='Pilot:',
                                   options=self.available_pilots)
        self.select_pilot.on_change('value', self.on_pilot_change)

        # Select - Session
        self.select_session = Select(title='Session:')
        self.select_session.on_change('value', self.on_session_change)

        # Select - Run
        self.select_run = Select(title="Run:")
        self.select_run.on_change('value', self.on_run_change)

        # Select - Model
        self.select_model = Select(title="Pre-trained model:",
                                   options=self.available_models)
        self.select_model.on_change('value', self.on_model_change)

        # Checkbox - Preprocessing
        self.div_preproc = Div(text='<b>Preprocessing</b>', align='center')
        self.checkbox_preproc = CheckboxButtonGroup(labels=['Filter',
                                                            'Standardize',
                                                            'Rereference'])

        self.slider_win_len = Slider(start=0.5, end=4, value=1,
                                     step=0.25, title='Win len (s)')

        self.checkbox_settings = CheckboxButtonGroup(labels=['Ensemble'])

        self.button_validate = Button(label='Validate',
                                      button_type='primary')
        self.button_validate.on_click(self.on_validate_start)

        self.div_info = Div()

        self.plot_chronogram = figure(title='Chronogram',
                                      x_axis_label='Time [s]',
                                      y_axis_label='Action',
                                      plot_height=300,
                                      plot_width=800)
        self.plot_chronogram.line(x='ts', y='y_true', color='blue',
                                  source=self.chrono_source,
                                  legend_label='Groundtruth')
        self.plot_chronogram.cross(x='ts', y='y_pred', color='red',
                                   source=self.chrono_source,
                                   legend_label='Prediction')
        self.plot_chronogram.legend.background_fill_alpha = 0.8
        self.plot_chronogram.yaxis.ticker = list(self.pred_decoding.keys())
        self.plot_chronogram.yaxis.major_label_overrides = self.pred_decoding

        column1 = column(self.select_pilot, self.select_session,
                         self.select_run,
                         self.select_model, self.div_preproc,
                         self.checkbox_preproc, self.checkbox_settings,
                         self.button_validate,
                         self.div_info)
        column2 = column(self.plot_chronogram)

        return row(column1, column2)
