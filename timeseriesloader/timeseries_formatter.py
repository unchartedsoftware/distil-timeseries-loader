"""
   Copyright © 2019 Uncharted Software Inc.

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""

import typing
import os
import csv
import collections

import frozendict  # type: ignore
import pandas as pd  # type: ignore

from d3m import container, exceptions, utils as d3m_utils
from d3m.metadata import base as metadata_base, hyperparams
from d3m.primitive_interfaces import base, transformer

__all__ = ('TimeSeriesFormatterPrimitive',)


class Hyperparams(hyperparams.Hyperparams):
    file_col_index = hyperparams.Hyperparameter[typing.Union[int, None]](
        default=None,
        semantic_types=['https://metadata.datadrivendiscovery.org/types/ControlParameter'],
        description='Index of column in input dataset containing time series file names.' +
                    'If set to None, will use the first csv filename column found.'
    )
    main_resource_index = hyperparams.Hyperparameter[typing.Union[str, None]](
        default='1',
        semantic_types=['https://metadata.datadrivendiscovery.org/types/ControlParameter'],
        description='Index of data resource in input dataset containing the reference to timeseries data.'
    )


class TimeSeriesFormatterPrimitive(transformer.TransformerPrimitiveBase[container.Dataset,
                                                                     container.Dataset,
                                                                     Hyperparams]):
    """
    Reads the time series files from a given column in an input dataset resource into a new M x N data resource,
    where each value in timeseries occupies one of M rows. Each row has N columns, representing the union of
    the fields found in the timeseries files and in the main data resource.
    The loading process assumes that each series file has an identical set of timestamps.
    """

    _semantic_types = ('https://metadata.datadrivendiscovery.org/types/FileName',
                       'https://metadata.datadrivendiscovery.org/types/Timeseries',
                       'http://schema.org/Text',
                       'https://metadata.datadrivendiscovery.org/types/Attribute')
    _media_types = ('text/csv',)

    __author__ = 'Uncharted Software',
    metadata = metadata_base.PrimitiveMetadata(
        {
            'id': '24b09066-836f-4b8f-9773-8c86a5eee26c',
            'version': '0.2.0',
            'name': 'Time series formatter',
            'python_path': 'd3m.primitives.data_preprocessing.timeseries_formatter.DistilTimeSeriesFormatter',
            'keywords': ['series', 'reader', 'csv'],
            'source': {
                'name': 'Uncharted Software',
                'contact': 'mailto:chris.bethune@uncharted.software',
                'uris': ['https://gitlab.com/uncharted-distil/distil-timeseries-loader']
            },
            'installation': [{
                'type': metadata_base.PrimitiveInstallationType.PIP,
                'package_uri': 'git+https://gitlab.com/uncharted-distil/distil-timeseries-loader.git@' +
                               '{git_commit}#egg=DistilTimeSeriesLoader-0.2.0'
                               .format(git_commit=d3m_utils.current_git_commit(os.path.dirname(__file__)),),
            }],
            'algorithm_types': [
                metadata_base.PrimitiveAlgorithmType.FILE_MANIPULATION,
            ],
            'supported_media_types': _media_types,
            'primitive_family': metadata_base.PrimitiveFamily.DATA_PREPROCESSING,
        }
    )

    @classmethod
    def _find_csv_file_column(cls, inputs_metadata: metadata_base.DataMetadata, res_id: int) -> typing.Optional[int]:
        indices = inputs_metadata.list_columns_with_semantic_types(cls._semantic_types, at=(res_id,))
        for i in indices:
            if cls._is_csv_file_column(inputs_metadata, res_id, i):
                return i
        return None

    @classmethod
    def _is_csv_file_column(cls, inputs_metadata: metadata_base.DataMetadata, res_id: int, column_index: int) -> bool:
        # check to see if a given column is a file pointer that points to a csv file
        column_metadata = inputs_metadata.query((res_id, metadata_base.ALL_ELEMENTS, column_index))

        if not column_metadata or column_metadata['structural_type'] != str:
            return False

        # check if a foreign key exists
        if column_metadata['foreign_key'] is None:
            return False

        ref_col_index = column_metadata['foreign_key']['column_index']
        ref_res_id = column_metadata['foreign_key']['resource_id']

        return cls._is_csv_file_reference(inputs_metadata, ref_res_id, ref_col_index)

    @classmethod
    def _is_csv_file_reference(cls, inputs_metadata: metadata_base.DataMetadata, res_id: int, column_index: int) -> bool:
        # check to see if the column is a csv resource
        column_metadata = inputs_metadata.query((res_id, metadata_base.ALL_ELEMENTS, column_index))

        if not column_metadata or column_metadata['structural_type'] != str:
            return False

        semantic_types = column_metadata.get('semantic_types', [])
        media_types = column_metadata.get('media_types', [])

        semantic_types_set = set(semantic_types)
        _semantic_types_set = set(cls._semantic_types)

        return bool(semantic_types_set.intersection(_semantic_types_set)) and set(cls._media_types).issubset(media_types)

    def produce(self, *,
                inputs: container.Dataset,
                timeout: float = None,
                iterations: int = None) -> base.CallResult[container.Dataset]:

        main_resource_index = self.hyperparams['main_resource_index']
        if main_resource_index is None:
            raise exceptions.InvalidArgumentValueError('no main resource specified')

        file_index = self.hyperparams['file_col_index']
        if file_index is not None:
            if not self._is_csv_file_column(inputs.metadata, main_resource_index, file_index):
                raise exceptions.InvalidArgumentValueError('column idx=' + str(file_index) + ' from does not contain csv file names')
        else:
            file_index = self._find_csv_file_column(inputs.metadata)
            if file_index is None:
                raise exceptions.InvalidArgumentValueError('no column from contains csv file names')

        # generate the long form timeseries data
        base_path = self._get_base_path(inputs.metadata, main_resource_index, file_index)
        output_data = []
        timeseries_dataframe = pd.DataFrame()
        for idx, tRow in inputs[main_resource_index].iterrows():
            # read the timeseries data
            csv_path = os.path.join(base_path, tRow[file_index])
            timeseries_row = pd.read_csv(csv_path)

            # add the timeseries id
            tRow = tRow.append(pd.Series({'series_id': int(idx)}))

            # combine the timeseries data with the value row
            output_data.extend([pd.concat([tRow, vRow]) for vIdx, vRow in timeseries_row.iterrows()])

        # add the timeseries index
        timeseries_dataframe = timeseries_dataframe.append(output_data, ignore_index=True)

        # join the metadata from the 2 data resources
        timeseries_dataframe = container.DataFrame(timeseries_dataframe)

        # wrap as a D3M container
        #return base.CallResult(container.Dataset({'0': timeseries_dataframe}, metadata))
        return base.CallResult(container.Dataset({'0': timeseries_dataframe}, generate_metadata=True))

    def _get_base_path(self,
                   inputs_metadata: metadata_base.DataMetadata,
                   res_id: str,
                   column_index: int) -> str:
        # get the base uri from the referenced column
        column_metadata = inputs_metadata.query((res_id, metadata_base.ALL_ELEMENTS, column_index))

        ref_col_index = column_metadata['foreign_key']['column_index']
        ref_res_id = column_metadata['foreign_key']['resource_id']

        return inputs_metadata.query((ref_res_id, metadata_base.ALL_ELEMENTS, ref_col_index))['location_base_uris'][0]

    def _get_ref_resource(self,
                   inputs_metadata: metadata_base.DataMetadata,
                   res_id: str,
                   column_index: int) -> str:
        # get the referenced resource from the referenced column
        column_metadata = inputs_metadata.query((res_id, metadata_base.ALL_ELEMENTS, column_index))
        ref_res_id = column_metadata['foreign_key']['resource_id']

        return ref_res_id
