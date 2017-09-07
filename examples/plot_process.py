"""
=================================================================
Tutorial 5: Formalizing Data Processing
=================================================================

**Suhas Somnath**

9/8/2017


This set of tutorials will serve as examples for developing end-to-end workflows for and using pycroscopy.

**In this example, we will learn how to write a simple yet formal pycroscopy class for processing data.**

Introduction
============

Data processing / analysis typically involves a few basic tasks:
1. Reading data from file
2. Computation
3. Writing results to disk

This example is based on the parallel computing example where we fit a dataset containing spectra at each location to a
function. While the previous example focused on comparing serial and parallel computing, we will focus on the framework
that needs to be built around a computation for robust data processing. As the example will show below, the framework
essentially deals with careful file reading and writing.

The majority of the code for this example is based on the BESHOModel Class under pycroscopy.analysis
"""

# Ensure python 3 compatibility:
from __future__ import division, print_function, absolute_import, unicode_literals

# The package for accessing files in directories, etc.:
import os
import wget

# The mathematical computation package:
import numpy as np
from numpy import exp, abs, sqrt, sum, real, imag, arctan2, append

# The package used for creating and manipulating HDF5 files:
import h5py

# Packages for plotting:
import matplotlib.pyplot as plt

# Finally import pycroscopy for certain scientific analysis:
import pycroscopy as px

field_names = ['Amplitude [V]', 'Frequency [Hz]', 'Quality Factor', 'Phase [rad]']
sho32 = np.dtype({'names': field_names,
                  'formats': [np.float32 for name in field_names]})


class ShoGuess(px.Process):

    def __init__(self, h5_main, cores=None):
        super(ShoGuess, self).__init__(h5_main, cores)

        # find the frequency vector
        h5_spec_vals = px.hdf_utils.getAuxData(h5_main, 'Spectroscopic_Values')[-1]
        self.freq_vec = np.squeeze(h5_spec_vals.value) * 1E-3

    def _create_results_datasets(self):
        h5_spec_inds = px.hdf_utils.getAuxData(self.h5_main, auxDataName=['Spectroscopic_Indices'])[0]
        h5_spec_vals = px.hdf_utils.getAuxData(self.h5_main, auxDataName=['Spectroscopic_Values'])[0]

        self.step_start_inds = np.where(h5_spec_inds[0] == 0)[0]
        self.num_udvs_steps = len(self.step_start_inds)
        
        ds_guess = px.MicroDataset('Guess', data=[],
                                             maxshape=(self.h5_main.shape[0], self.num_udvs_steps),
                                             chunking=(1, self.num_udvs_steps), dtype=sho32)

        not_freq = px.hdf_utils.get_attr(h5_spec_inds, 'labels') != 'Frequency'

        ds_sho_inds, ds_sho_vals = px.hdf_utils.buildReducedSpec(h5_spec_inds, h5_spec_vals, not_freq,
                                                                 self.step_start_inds)

        dset_name = self.h5_main.name.split('/')[-1]
        sho_grp = px.MicroDataGroup('-'.join([dset_name, 'SHO_Fit_']), self.h5_main.parent.name[1:])
        sho_grp.addChildren([ds_guess, ds_sho_inds, ds_sho_vals])
        sho_grp.attrs['SHO_guess_method'] = "pycroscopy BESHO"

        h5_sho_grp_refs = self.hdf.writeData(sho_grp)

        self.h5_guess = px.hdf_utils.getH5DsetRefs(['Guess'], h5_sho_grp_refs)[0]
        self.h5_results_grp = self.h5_guess.parent
        h5_sho_inds = px.hdf_utils.getH5DsetRefs(['Spectroscopic_Indices'],
                                                 h5_sho_grp_refs)[0]
        h5_sho_vals = px.hdf_utils.getH5DsetRefs(['Spectroscopic_Values'],
                                                 h5_sho_grp_refs)[0]

        # Reference linking before actual fitting
        px.hdf_utils.linkRefs(self.h5_guess, [h5_sho_inds, h5_sho_vals])
        # Linking ancillary position datasets:
        aux_dsets = px.hdf_utils.getAuxData(self.h5_main, auxDataName=['Position_Indices', 'Position_Values'])
        px.hdf_utils.linkRefs(self.h5_guess, aux_dsets)
        print('Finshed creating datasets')

    def compute(self, *args, **kwargs):
        # here we simply extend the existing compute function and only pass the parameters for the unit function
        # In this case, the only parameter is the frequency vector.
        return super(ShoGuess, self).compute(w_vec=self.freq_vec)

    def _write_results_chunk(self):
        # converting from a list to a 2D numpy array
        self._results = np.array(self._results, dtype=np.float32)
        self.h5_guess[:, 0] = px.io_utils.realToCompound(self._results, sho32)

        # Now update the start position
        self._start_pos = self._end_pos
        # this should stop the computation.

    @staticmethod
    def sho_function(parms, w_vec):
        """
        Generates the SHO response over the given frequency band

        Parameters
        -----------
        parms : list or tuple
            SHO parameters=(Amplitude, frequency ,Quality factor, phase)
        w_vec : 1D numpy array
            Vector of frequency values
        """
        return parms[0] * exp(1j * parms[3]) * parms[1] ** 2 / (
            w_vec ** 2 - 1j * w_vec * parms[1] / parms[2] - parms[1] ** 2)

    @staticmethod
    def _unit_function(resp_vec, w_vec=None, num_points=5):
        """
        Generates good initial guesses for fitting

        Parameters
        ------------
        resp_vec : 1D complex numpy array or list
            BE response vector as a function of frequency
        w_vec : 1D numpy array or list, Optional
            Vector of BE frequencies
        num_points : (Optional) unsigned int
            Quality factor of the SHO peak

        Returns
        ---------
        retval : tuple
            SHO fit parameters arranged as amplitude, frequency, quality factor, phase
        """
        if w_vec is None:
            # Some default value
            w_vec = np.linspace(300E+3, 350E+3, resp_vec.size)

        ii = np.argsort(abs(resp_vec))[::-1]

        a_mat = np.array([])
        e_vec = np.array([])

        for c1 in range(num_points):
            for c2 in range(c1 + 1, num_points):
                w1 = w_vec[ii[c1]]
                w2 = w_vec[ii[c2]]
                X1 = real(resp_vec[ii[c1]])
                X2 = real(resp_vec[ii[c2]])
                Y1 = imag(resp_vec[ii[c1]])
                Y2 = imag(resp_vec[ii[c2]])

                denom = (w1 * (X1 ** 2 - X1 * X2 + Y1 * (Y1 - Y2)) + w2 * (-X1 * X2 + X2 ** 2 - Y1 * Y2 + Y2 ** 2))
                if denom > 0:
                    a = ((w1 ** 2 - w2 ** 2) * (w1 * X2 * (X1 ** 2 + Y1 ** 2) - w2 * X1 * (X2 ** 2 + Y2 ** 2))) / denom
                    b = ((w1 ** 2 - w2 ** 2) * (w1 * Y2 * (X1 ** 2 + Y1 ** 2) - w2 * Y1 * (X2 ** 2 + Y2 ** 2))) / denom
                    c = ((w1 ** 2 - w2 ** 2) * (X2 * Y1 - X1 * Y2)) / denom
                    d = (w1 ** 3 * (X1 ** 2 + Y1 ** 2) -
                         w1 ** 2 * w2 * (X1 * X2 + Y1 * Y2) -
                         w1 * w2 ** 2 * (X1 * X2 + Y1 * Y2) +
                         w2 ** 3 * (X2 ** 2 + Y2 ** 2)) / denom

                    if d > 0:
                        a_mat = append(a_mat, [a, b, c, d])

                        A_fit = abs(a + 1j * b) / d
                        w0_fit = sqrt(d)
                        Q_fit = -sqrt(d) / c
                        phi_fit = arctan2(-b, -a)

                        H_fit = A_fit * w0_fit ** 2 * exp(1j * phi_fit) / (
                            w_vec ** 2 - 1j * w_vec * w0_fit / Q_fit - w0_fit ** 2)

                        e_vec = append(e_vec,
                                       sum((real(H_fit) - real(resp_vec)) ** 2) +
                                       sum((imag(H_fit) - imag(resp_vec)) ** 2))
        if a_mat.size > 0:
            a_mat = a_mat.reshape(-1, 4)

            weight_vec = (1 / e_vec) ** 4
            w_sum = sum(weight_vec)

            a_w = sum(weight_vec * a_mat[:, 0]) / w_sum
            b_w = sum(weight_vec * a_mat[:, 1]) / w_sum
            c_w = sum(weight_vec * a_mat[:, 2]) / w_sum
            d_w = sum(weight_vec * a_mat[:, 3]) / w_sum

            A_fit = abs(a_w + 1j * b_w) / d_w
            w0_fit = sqrt(d_w)
            Q_fit = -sqrt(d_w) / c_w
            phi_fit = np.arctan2(-b_w, -a_w)

            H_fit = A_fit * w0_fit ** 2 * exp(1j * phi_fit) / (w_vec ** 2 - 1j * w_vec * w0_fit / Q_fit - w0_fit ** 2)

            if np.std(abs(resp_vec)) / np.std(abs(resp_vec - H_fit)) < 1.2 or w0_fit < np.min(w_vec) or w0_fit > np.max(
                    w_vec):
                p0 = ShoGuess.sho_fast_guess(w_vec, resp_vec)
            else:
                p0 = np.array([A_fit, w0_fit, Q_fit, phi_fit])
        else:
            p0 = ShoGuess.sho_fast_guess(resp_vec, w_vec)

        return p0

    @staticmethod
    def sho_fast_guess(resp_vec, w_vec, qual_factor=200):
        """
        Default SHO guess from the maximum value of the response

        Parameters
        ------------
        resp_vec : 1D complex numpy array or list
            BE response vector as a function of frequency
        w_vec : 1D numpy array or list
            Vector of BE frequencies
        qual_factor : float
            Quality factor of the SHO peak

        Returns
        -------
        retval : 1D numpy array
            SHO fit parameters arranged as [amplitude, frequency, quality factor, phase]
        """
        amp_vec = abs(resp_vec)
        i_max = int(len(resp_vec) / 2)
        return np.array([np.mean(amp_vec) / qual_factor, w_vec[i_max], qual_factor, np.angle(resp_vec[i_max])])

#########################################################################
# Load the dataset
# ================
#
# For this example, we will be working with a Band Excitation Piezoresponse Force Microscopy (BE-PFM) imaging dataset
# acquired from advanced atomic force microscopes. In this dataset, a spectra was collected for each position in a two
# dimensional grid of spatial locations. Thus, this is a three dimensional dataset that has been flattened to a two
# dimensional matrix in accordance with the pycroscopy data format.

# download the raw data file from Github:
h5_path = 'temp.h5'
url = 'https://raw.githubusercontent.com/pycroscopy/pycroscopy/master/data/BELine_0004.h5'
if os.path.exists(h5_path):
    os.remove(h5_path)
_ = wget.download(url, h5_path)

#########################################################################

# Open the file in read-only mode
h5_file = h5py.File(h5_path, mode='r+')

# Get handles to the the raw data along with other datasets and datagroups that contain necessary parameters
h5_meas_grp = h5_file['Measurement_000']
num_rows = px.hdf_utils.get_attr(h5_meas_grp, 'grid_num_rows')
num_cols = px.hdf_utils.get_attr(h5_meas_grp, 'grid_num_cols')

# Getting a reference to the main dataset:
h5_main = h5_meas_grp['Channel_000/Raw_Data']

# Extracting the X axis - vector of frequencies
h5_spec_vals = px.hdf_utils.getAuxData(h5_main, 'Spectroscopic_Values')[-1]
freq_vec = np.squeeze(h5_spec_vals.value) * 1E-3

fitter = ShoGuess(h5_main, cores=4)
h5_results_grp = fitter.compute()
h5_guess = h5_results_grp['Guess']

row_ind, col_ind = 103, 19
pix_ind = col_ind + row_ind * num_cols
resp_vec = h5_main[pix_ind]
norm_guess_parms = h5_guess[pix_ind]
# Converting from compound to real:
norm_guess_parms = px.io_utils.compound_to_scalar(norm_guess_parms)
print('Functional fit returned:', norm_guess_parms)
norm_resp = fitter.sho_function(norm_guess_parms, freq_vec)

fig, axes = plt.subplots(nrows=2, sharex=True, figsize=(5, 10))
for axis, func, title in zip(axes.flat, [np.abs, np.angle], ['Amplitude (a.u.)', 'Phase (rad)']):
    axis.scatter(freq_vec, func(resp_vec), c='red', label='Measured')
    axis.plot(freq_vec, func(norm_resp), 'black', lw=3, label='Guess')
    axis.set_title(title, fontsize=16)
    axis.legend(fontsize=14)

axes[1].set_xlabel('Frequency (kHz)', fontsize=14)
axes[0].set_ylim([0, np.max(np.abs(resp_vec)) * 1.1])
axes[1].set_ylim([-np.pi, np.pi])

#########################################################################
# **Delete the temporarily downloaded file**

h5_file.close()
os.remove(h5_path)