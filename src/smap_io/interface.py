# The MIT License (MIT)
#
# Copyright (c) 2016,TU Wien
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
'''
Module to read single SMAP L3 images and image stacks
'''

import os
from pygeobase.io_base import ImageBase, MultiTemporalImageBase
from pygeobase.object_base import Image
from pynetcf.time_series import GriddedNcOrthoMultiTs
import pygeogrids.netcdf as ncdf
import h5py
import numpy as np
from parse import *
from datetime import timedelta
import warnings
from smap_io.grid import EASE36CellGrid


class SPL3SMP_Img(ImageBase):
    """
    Class for reading one image of SMAP Level 3 version 5 Passive Soil Moisture

    Parameters
    ----------
    filename: str
        filename of the SMAP h5 file to read.
    mode: str, optional (default: 'r')
        mode of opening the file, only 'r' is implemented at the moment
    parameter : str or list, optional (default : 'soil_moisture')
        one or list of parameters found at http://nsidc.org/data/smap_io/spl3smp/data-fields
    overpass : str, optional (default: 'AM')
        Select 'AM' for the descending overpass or 'PM' for the ascending one.
        If there is only one overpass in the file (old SPL3 versions) pass None.
        Passing PM will result in reading variables called *name*_pm
        Passing AM will result in reading variables called *name*
    var_overpass_str : bool, optional (default: True)
        Append overpass indicator to the loaded variables. E.g. Soil Moisture
        will be called soil_moisture_pm and soil_moisture_am, and soil_moisture
        in all cases if this is set to False.
    grid: pygeogrids.CellGrid, optional (default: None)
        A (sub)grid of points to read. e.g. to read data for land points only
        for a specific bounding box. Must be a subgrid of an EASE25 Grid.
        If None is passed, all point are read.
    flatten: bool, optional (default: False)
        If true the read data will be returned as 1D arrays. Where the first
        value refers to the bottom-left most point in the grid!
        If not flattened, a 2d array where the min Lat is in the bottom row
        is returned!
    """

    def __init__(self,
                 filename,
                 mode='r',
                 parameter='soil_moisture',
                 overpass='AM',
                 var_overpass_str=True,
                 grid=None,
                 flatten=False):

        super().__init__(filename, mode=mode)

        self.grid = EASE36CellGrid() if grid is None else grid

        if type(parameter) != list:
            parameter = [parameter]

        self.overpass = overpass.upper() if overpass is not None else None
        self.overpass_templ = 'Soil_Moisture_Retrieval_Data{orbit}'
        self.var_overpass_str = var_overpass_str
        self.parameters = parameter
        self.flatten = flatten

    def read(self, timestamp=None) -> Image:
        """
        Read a single h5 image file to pygeobase Image.

        Parameters
        ----------
        timestamp: datetime, optional (default: False)
            Time stamp to read. If None is passed, the Image will
            not have a time stamp assigned.
        """

        return_data = {}
        return_meta = {}

        try:
            ds = h5py.File(self.filename, mode='r')
        except IOError as e:
            print(e)
            print(" ".join([self.filename, "can not be opened"]))
            raise e

        if self.overpass is None:
            overpasses = []
            for k in list(ds.keys()):
                p = parse(self.overpass_templ, k)
                if p is not None and ('orbit' in p.named.keys()):
                    overpasses.append(p['orbit'][1:])  # omit leading _

            if len(overpasses) > 1:
                raise IOError(
                    'Multiple overpasses found in file, please specify '
                    f'one overpass to load: {overpasses}')
            else:
                self.overpass = overpasses[0].upper()
        else:
            assert self.overpass in ['AM', 'PM']

        overpass = self.overpass

        overpass_str = '_' + overpass.upper() if overpass else ''
        sm_field = self.overpass_templ.format(orbit=overpass_str)

        if sm_field not in ds.keys():
            raise NameError(
                sm_field,
                'Field does not exists. Try deactivating overpass option.')

        if overpass:
            overpass_str = '_pm' if overpass == 'PM' else ''
        else:
            overpass_str = ''

        for parameter in self.parameters:
            metadata = {}
            param = ds[sm_field][parameter + overpass_str]
            data = np.flipud(param[:]).flatten()

            if self.grid is not None:
                data = data[self.grid.activegpis]
            # mask according to valid_min, valid_max and _FillValue
            try:
                fill_value = param.attrs['_FillValue']
                valid_min = param.attrs['valid_min']
                valid_max = param.attrs['valid_max']
                data = np.where(
                    np.logical_or(data < valid_min, data > valid_max),
                    fill_value, data)
            except KeyError:
                pass

            # fill metadata dictionary with metadata from image
            for key in param.attrs:
                metadata[key] = param.attrs[key]

            ret_param_name = parameter

            if self.var_overpass_str:
                if overpass is None:
                    warnings.warn(
                        'Renaming variable only possible if overpass in given.'
                        ' Use names as in file.')
                    ret_param_name = parameter
                elif not parameter.endswith(f'_{overpass.lower()}'):
                    ret_param_name = parameter + f'_{overpass.lower()}'

            return_data[ret_param_name] = data
            return_meta[ret_param_name] = metadata

        if self.flatten:
            return Image(self.grid.activearrlon, self.grid.activearrlat,
                         return_data, return_meta, timestamp)
        else:

            if len(self.grid.subset_shape) != 2:
                raise ValueError(
                    "Grid is 1-dimensional, to read a 2d image,"
                    " a 2d grid - e.g. from bbox of the global grid -"
                    "is required.")

            if (np.prod(self.grid.subset_shape) != len(
                    self.grid.activearrlon)) or \
                    (np.prod(self.grid.subset_shape) != len(
                        self.grid.activearrlat)):
                raise ValueError(
                    f"The grid shape {self.grid.subset_shape} "
                    f"does not match with the shape of the loaded "
                    f"data. If you have passed a subgrid with gaps"
                    f" (e.g. landpoints only) you have to set"
                    f" `flatten=True`")

            lons = np.flipud(
                self.grid.activearrlon.reshape(self.grid.subset_shape))
            lats = np.flipud(
                self.grid.activearrlat.reshape(self.grid.subset_shape))
            data = {
                param: np.flipud(data.reshape(self.grid.subset_shape))
                for param, data in return_data.items()
            }

            return Image(lons, lats, data, return_meta, timestamp)

    def write(self, data):
        raise NotImplementedError()

    def flush(self):
        pass

    def close(self):
        pass


class SPL3SMP_Ds(MultiTemporalImageBase):
    """
    Class for reading a collection of SMAP Level 3 Passive Soil Moisture images.

    Parameters
    ----------
    data_path: str
        root path of the SMAP data files
    parameter : str or list, optional (default: 'soil_moisture')
        one or list of parameters found at http://nsidc.org/data/smap_io/spl3smp/data-fields
        Default : 'soil_moisture'
    overpass : str, optional (default: 'AM')
        Select 'AM' for the descending overpass or 'PM' for the ascending one.
        Dataset version must support multiple overpasses.
    var_overpass_str : bool, optional (default: True)
        Append overpass indicator to the loaded variables. E.g. Soil Moisture
        will be called soil_moisture_pm and soil_moisture_am, and soil_moisture
        in all cases if this is set to False.
    subpath_templ : list, optional (default: ('%Y.%m.%d',))
        If the data is store in subpaths based on the date of the dataset then this list
        can be used to specify the paths. Every list element specifies one path level.
    crid : int, optional (default: None)
        Only read files with this specific Composite Release ID.
        See also https://nsidc.org/data/smap/data_versions#CRID
    grid: pygeogrids.CellGrid, optional (default: None)
        A (sub)grid of points to read. e.g. to read data for land points only
        for a specific bounding box. Must be a subgrid of an EASE25 Grid.
        If None is passed, all point are read.
    flatten: bool, optional (default: False)
        If true the read data will be returned as 1D arrays.
    """

    def __init__(self,
                 data_path,
                 subpath_templ=('%Y.%m.%d',),
                 crid=None,
                 parameter='soil_moisture',
                 overpass='AM',
                 var_overpass_str=True,
                 grid=None,
                 flatten=False):

        if crid is None:
            filename_templ = f"SMAP_L3_SM_P_{'{datetime}'}_*.h5"
        else:
            filename_templ = f"SMAP_L3_SM_P_{'{datetime}'}_R{crid}*.h5"

        ioclass_kws = {
            'parameter': parameter,
            'overpass': overpass,
            'var_overpass_str': var_overpass_str,
            'grid': grid,
            'flatten': flatten
        }

        super().__init__(
            data_path,
            SPL3SMP_Img,
            fname_templ=filename_templ,
            datetime_format="%Y%m%d",
            subpath_templ=subpath_templ,
            exact_templ=False,
            ioclass_kws=ioclass_kws)

    def tstamps_for_daterange(self, start_date, end_date):
        """
        return timestamps for daterange,

        Parameters
        ----------
        start_date: datetime
            start of date range
        end_date: datetime
            end of date range

        Returns
        -------
        timestamps : list
            list of datetime objects of each available image between
            start_date and end_date
        """
        timestamps = []
        diff = end_date - start_date
        for i in range(diff.days + 1):
            daily_date = start_date + timedelta(days=i)
            timestamps.append(daily_date)

        return timestamps


class SMAPTs(GriddedNcOrthoMultiTs):

    def __init__(self, ts_path, grid_path=None, **kwargs):
        """
        Class for reading SMAP time series after reshuffling.

        Parameters
        ----------
        ts_path : str
            Directory where the netcdf time series files are stored
        grid_path : str, optional (default: None)
            Path to grid file, that is used to organize the location of time
            series to read. If None is passed, grid.nc is searched for in the
            ts_path.

        Optional keyword arguments that are passed to the Gridded Base:
        ------------------------------------------------------------------------
            parameters : list, optional (default: None)
                Specific variable names to read, if None are selected, all are read.
            offsets : dict, optional (default:None)
                Offsets (values) that are added to the parameters (keys)
            scale_factors : dict, optional (default:None)
                Offset (value) that the parameters (key) is multiplied with
            ioclass_kws: dict, (optional)
                Optional keyword arguments to pass to OrthoMultiTs class:
                ----------------------------------------------------------------
                    read_bulk : boolean, optional (default:False)
                        if set to True the data of all locations is read into memory,
                        and subsequent calls to read_ts read from the cache and
                        not from disk this makes reading complete files faster
                    read_dates : boolean, optional (default:False)
                        if false dates will not be read automatically but only on
                        specific request useable for bulk reading because currently
                        the netCDF num2date routine is very slow for big datasets.
        """

        if grid_path is None:
            grid_path = os.path.join(ts_path, "grid.nc")

        grid = ncdf.load_grid(grid_path)
        super(SMAPTs, self).__init__(ts_path, grid, **kwargs)


if __name__ == '__main__':
    path = "/home/wpreimes/shares/radar/Datapool/SMAP/01_raw/SPL3SMP_v6/"
    grid = EASE36CellGrid(only_land=True)
    img = SPL3SMP_Img(
        filename="/home/wpreimes/shares/radar/Datapool/SMAP/01_raw/SPL3SMP_v6/2020.05.15/SMAP_L3_SM_P_20200515_R16515_001.h5",
        grid=grid,
        flatten=True)
    dat = img.read()
