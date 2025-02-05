"""
Module responsible for translating variant data into GA4GH native
objects.
"""
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import datetime
import glob
import hashlib
import json
import os
import random
import re

import pysam

import ga4gh.protocol as protocol
import ga4gh.exceptions as exceptions
import ga4gh.datamodel as datamodel

ANNOTATIONS_VEP_V82 = "VEP_v82"
ANNOTATIONS_VEP_V77 = "VEP_v77"
ANNOTATIONS_SNPEFF = "SNPEff"


def isUnspecified(str):
    """
    Checks whether a string is None or an
    empty string. Returns a boolean.
    """
    return str == "" or str is None


class CallSet(datamodel.DatamodelObject):
    """
    Class representing a CallSet. A CallSet basically represents the
    metadata associated with a single VCF sample column.
    """
    compoundIdClass = datamodel.CallSetCompoundId

    def populateFromRow(self, row):
        """
        Populates this CallSet from the specified DB row.
        """
        # currently a noop

    def toProtocolElement(self):
        """
        Returns the representation of this CallSet as the corresponding
        ProtocolElement.
        """
        variantSet = self.getParentContainer()
        gaCallSet = protocol.CallSet()
        gaCallSet.created = variantSet.getCreationTime()
        gaCallSet.updated = variantSet.getUpdatedTime()
        gaCallSet.id = self.getId()
        gaCallSet.name = self.getLocalId()
        gaCallSet.sampleId = self.getLocalId()
        gaCallSet.variantSetIds = [variantSet.getId()]
        return gaCallSet

    def getSampleName(self):
        """
        Returns the sample name for this CallSet.
        """
        return self.getLocalId()


class AbstractVariantSet(datamodel.DatamodelObject):
    """
    An abstract base class of a variant set
    """
    compoundIdClass = datamodel.VariantSetCompoundId

    def __init__(self, parentContainer, localId):
        super(AbstractVariantSet, self).__init__(parentContainer, localId)
        self._callSetIdMap = {}
        self._callSetNameMap = {}
        self._callSetIds = []
        self._callSetIdToIndex = {}
        self._creationTime = None
        self._updatedTime = None
        self._referenceSet = None
        self._variantAnnotationSetIds = []
        self._variantAnnotationSetIdMap = {}

    def addVariantAnnotationSet(self, variantAnnotationSet):
        """
        Adds the specified variantAnnotationSet to this dataset.
        """
        id_ = variantAnnotationSet.getId()
        self._variantAnnotationSetIdMap[id_] = variantAnnotationSet
        self._variantAnnotationSetIds.append(id_)

    def getVariantAnnotationSets(self):
        """
        Returns the list of VariantAnnotationSets in this dataset
        """
        return [
            self._variantAnnotationSetIdMap[id_] for id_ in
            self._variantAnnotationSetIds]

    def getVariantAnnotationSet(self, id_):
        """
        Returns the AnnotationSet in this dataset with the specified 'id'
        """
        if id_ not in self._variantAnnotationSetIdMap:
            raise exceptions.AnnotationSetNotFoundException(id_)
        return self._variantAnnotationSetIdMap[id_]

    def getNumVariantAnnotationSets(self):
        """
        Returns the number of variant annotation sets in this dataset.
        """
        return len(self._variantAnnotationSetIds)

    def getVariantAnnotationSetByIndex(self, index):
        """
        Returns the variant annotation set at the specified index in this
        dataset.
        """
        return self._variantAnnotationSetIdMap[
            self._variantAnnotationSetIds[index]]

    def setReferenceSet(self, referenceSet):
        """
        Sets the ReferenceSet for this VariantSet to the specified value.
        """
        self._referenceSet = referenceSet

    def getReferenceSet(self):
        """
        Returns the reference set associated with this VariantSet.
        """
        return self._referenceSet

    def getCreationTime(self):
        """
        Returns the creation time for this variant set.
        """
        return self._creationTime

    def getUpdatedTime(self):
        """
        Returns the time this variant set was last updated.
        """
        return self._updatedTime

    def addCallSet(self, callSet):
        """
        Adds the specfied CallSet to this VariantSet.
        """
        callSetId = callSet.getId()
        self._callSetIdMap[callSetId] = callSet
        self._callSetNameMap[callSet.getLocalId()] = callSet
        self._callSetIds.append(callSetId)
        self._callSetIdToIndex[callSet.getId()] = len(self._callSetIds) - 1

    def addCallSetFromName(self, sampleName):
        """
        Adds a CallSet for the specified sample name.
        """
        callSet = CallSet(self, sampleName)
        self.addCallSet(callSet)

    def getCallSets(self):
        """
        Returns the list of CallSets in this VariantSet.
        """
        return [self._callSetIdMap[id_] for id_ in self._callSetIds]

    def getNumCallSets(self):
        """
        Returns the number of CallSets in this variant set.
        """
        return len(self._callSetIds)

    def getCallSetByName(self, name):
        """
        Returns a CallSet with the specified name, or raises a
        CallSetNameNotFoundException if it does not exist.
        """
        if name not in self._callSetNameMap:
            raise exceptions.CallSetNameNotFoundException(name)
        return self._callSetNameMap[name]

    def getCallSetByIndex(self, index):
        """
        Returns the CallSet at the specfied index in this VariantSet.
        """
        return self._callSetIdMap[self._callSetIds[index]]

    def getCallSet(self, id_):
        """
        Returns a CallSet with the specified id, or raises a
        CallSetNotFoundException if it does not exist.
        """
        if id_ not in self._callSetIdMap:
            raise exceptions.CallSetNotFoundException(id_)
        return self._callSetIdMap[id_]

    def toProtocolElement(self):
        """
        Converts this VariantSet into its GA4GH protocol equivalent.
        """
        protocolElement = protocol.VariantSet()
        protocolElement.id = self.getId()
        protocolElement.datasetId = self.getParentContainer().getId()
        protocolElement.referenceSetId = self._referenceSet.getId()
        protocolElement.metadata = self.getMetadata()
        protocolElement.name = self.getLocalId()
        return protocolElement

    def getNumVariants(self):
        """
        Returns the number of variants contained in this VariantSet.
        """
        raise NotImplementedError()

    def _createGaVariant(self):
        """
        Convenience method to set the common fields in a GA Variant
        object from this variant set.
        """
        ret = protocol.Variant()
        ret.created = self._creationTime
        ret.updated = self._updatedTime
        ret.variantSetId = self.getId()
        return ret

    def getVariantId(self, gaVariant):
        """
        Returns an ID string suitable for the specified GA Variant
        object in this variant set.
        """
        md5 = self.hashVariant(gaVariant)
        compoundId = datamodel.VariantCompoundId(
            self.getCompoundId(), gaVariant.referenceName,
            str(gaVariant.start), md5)
        return str(compoundId)

    def getCallSetId(self, sampleName):
        """
        Returns the callSetId for the specified sampleName in this
        VariantSet.
        """
        compoundId = datamodel.CallSetCompoundId(
            self.getCompoundId(), sampleName)
        return str(compoundId)

    @classmethod
    def hashVariant(cls, gaVariant):
        """
        Produces an MD5 hash of the ga variant object to distinguish
        it from other variants at the same genomic coordinate.
        """
        return hashlib.md5(
            gaVariant.referenceBases +
            str(tuple(gaVariant.alternateBases))).hexdigest()


class SimulatedVariantSet(AbstractVariantSet):
    """
    A variant set that doesn't derive from a data store.
    Used mostly for testing.
    """
    def __init__(
            self, parentContainer, referenceSet, localId, randomSeed=1,
            numCalls=1, variantDensity=1):
        super(SimulatedVariantSet, self).__init__(parentContainer, localId)
        self._referenceSet = referenceSet
        self._randomSeed = randomSeed
        self._numCalls = numCalls
        for j in range(numCalls):
            self.addCallSetFromName("simCallSet_{}".format(j))
        self._variantDensity = variantDensity
        now = protocol.convertDatetime(datetime.datetime.now())
        self._creationTime = now
        self._updatedTime = now

    def getNumVariants(self):
        return 0

    def getMetadata(self):
        ret = []
        # TODO Add simulated metadata.
        return ret

    def getVariant(self, compoundId):
        randomNumberGenerator = random.Random()
        start = int(compoundId.start)
        randomNumberGenerator.seed(self._randomSeed + start)
        variant = self.generateVariant(
            compoundId.referenceName, start, randomNumberGenerator)
        return variant

    def getVariants(self, referenceName, startPosition, endPosition,
                    callSetIds=None):
        randomNumberGenerator = random.Random()
        randomNumberGenerator.seed(self._randomSeed)
        i = startPosition
        while i < endPosition:
            if randomNumberGenerator.random() < self._variantDensity:
                randomNumberGenerator.seed(self._randomSeed + i)
                yield self.generateVariant(
                    referenceName, i, randomNumberGenerator)
            i += 1

    def generateVariant(self, referenceName, position, randomNumberGenerator):
        """
        Generate a random variant for the specified position using the
        specified random number generator. This generator should be seeded
        with a value that is unique to this position so that the same variant
        will always be produced regardless of the order it is generated in.
        """
        variant = self._createGaVariant()
        variant.names = []
        variant.referenceName = referenceName
        variant.start = position
        variant.end = position + 1  # SNPs only for now
        bases = ["A", "C", "G", "T"]
        ref = randomNumberGenerator.choice(bases)
        variant.referenceBases = ref
        alt = randomNumberGenerator.choice(
            [base for base in bases if base != ref])
        variant.alternateBases = [alt]
        variant.calls = []
        for callSet in self.getCallSets():
            call = protocol.Call()
            call.callSetId = callSet.getId()
            # for now, the genotype is either [0,1], [1,1] or [1,0] with equal
            # probability; probably will want to do something more
            # sophisticated later.
            randomChoice = randomNumberGenerator.choice(
                [[0, 1], [1, 0], [1, 1]])
            call.genotype = randomChoice
            # TODO What is a reasonable model for generating these likelihoods?
            # Are these log-scaled? Spec does not say.
            call.genotypeLikelihood = [-100, -100, -100]
            variant.calls.append(call)
        variant.id = self.getVariantId(variant)
        return variant


def _encodeValue(value):
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    else:
        return [str(value)]


_nothing = object()


def isEmptyIter(it):
    """Return True iff the iterator is empty or exhausted"""
    return next(it, _nothing) is _nothing


class HtslibVariantSet(datamodel.PysamDatamodelMixin, AbstractVariantSet):
    """
    Class representing a single variant set backed by a directory of indexed
    VCF or BCF files.
    """
    def __init__(self, parentContainer, localId):
        super(HtslibVariantSet, self).__init__(parentContainer, localId)
        self._chromFileMap = {}
        self._metadata = None

    def isAnnotated(self):
        """
        Returns True if there is a VariantAnnotationSet associated with this
        VariantSet.
        """
        return len(self._variantAnnotationSetIdMap) > 0

    def getReferenceToDataUrlIndexMap(self):
        """
        Returns the map of Reference names to the (dataUrl, indexFile) pairs.
        """
        return self._chromFileMap

    def getDataUrlIndexPairs(self):
        """
        Returns the set of (dataUrl, indexFile) pairs.
        """
        return set(self._chromFileMap.values())

    def populateFromRow(self, row):
        """
        Populates this VariantSet from the specified DB row.
        """
        self._created = row[b'created']
        self._updated = row[b'updated']
        self._chromFileMap = {}
        # We can't load directly as we want tuples to be stored
        # rather than lists.
        for key, value in json.loads(row[b'dataUrlIndexMap']).items():
            self._chromFileMap[key] = tuple(value)
        self._metadata = []
        for jsonDict in json.loads(row[b'metadata']):
            metadata = protocol.VariantSetMetadata.fromJsonDict(jsonDict)
            self._metadata.append(metadata)

    def populateFromFile(self, dataUrls, indexFiles):
        """
        Populates this variant set using the specified lists of data
        files and indexes. These must be in the same order, such that
        the jth index file corresponds to the jth data file.
        """
        assert len(dataUrls) == len(indexFiles)
        for dataUrl, indexFile in zip(dataUrls, indexFiles):
            varFile = pysam.VariantFile(dataUrl, index_filename=indexFile)
            try:
                self._populateFromVariantFile(varFile, dataUrl, indexFile)
            finally:
                varFile.close()

    def populateFromDirectory(self, vcfDirectory):
        """
        Populates this VariantSet by examing all the VCF files in the
        specified directory. This is mainly used for as a convenience
        for testing purposes.
        """
        pattern = os.path.join(vcfDirectory, "*.vcf.gz")
        dataFiles = []
        indexFiles = []
        for vcfFile in glob.glob(pattern):
            dataFiles.append(vcfFile)
            indexFiles.append(vcfFile + ".tbi")
        self.populateFromFile(dataFiles, indexFiles)

    def getVcfHeaderReferenceSetName(self):
        """
        Returns the name of the reference set from the VCF header.
        """
        # TODO implemenent
        return None

    def checkConsistency(self):
        """
        Perform consistency check on the variant set
        """
        for referenceName, (dataUrl, indexFile) in self._chromFileMap.items():
            varFile = pysam.VariantFile(dataUrl, index_filename=indexFile)
            try:
                for chrom in varFile.index:
                    chrom, _, _ = self.sanitizeVariantFileFetch(chrom)
                    if not isEmptyIter(varFile.fetch(chrom)):
                        self._checkMetadata(varFile)
                        self._checkCallSetIds(varFile)
            finally:
                varFile.close()

    def _populateFromVariantFile(self, varFile, dataUrl, indexFile):
        """
        Populates the instance variables of this VariantSet from the specified
        pysam VariantFile object.
        """
        if varFile.index is None:
            raise exceptions.NotIndexedException(dataUrl)
        for chrom in varFile.index:
            # Unlike Tabix indices, CSI indices include all contigs defined
            # in the BCF header.  Thus we must test each one to see if
            # records exist or else they are likely to trigger spurious
            # overlapping errors.
            chrom, _, _ = self.sanitizeVariantFileFetch(chrom)
            if not isEmptyIter(varFile.fetch(chrom)):
                if chrom in self._chromFileMap:
                    raise exceptions.OverlappingVcfException(dataUrl, chrom)
            self._chromFileMap[chrom] = dataUrl, indexFile
        self._updateMetadata(varFile)
        self._updateCallSetIds(varFile)
        self._updateVariantAnnotationSets(varFile, dataUrl)

    def _updateVariantAnnotationSets(self, variantFile, dataUrl):
        """
        Updates the variant annotation set associated with this variant using
        information in the specified pysam variantFile.
        """
        # TODO check the consistency of this between VCF files.
        if not self.isAnnotated():
            annotationType = None
            for record in variantFile.header.records:
                if record.type == "GENERIC":
                    if record.key == "SnpEffVersion":
                        annotationType = ANNOTATIONS_SNPEFF
                    elif record.key == "VEP":
                        version = record.value.split()[0]
                        # TODO we need _much_ more sophisticated processing
                        # of VEP versions here. When do they become
                        # incompatible?
                        if version == "v82":
                            annotationType = ANNOTATIONS_VEP_V82
                        elif version == "v77":
                            annotationType = ANNOTATIONS_VEP_V77
                        else:
                            # TODO raise a proper typed exception there with
                            # the file name as an argument.
                            raise ValueError(
                                "Unsupported VEP version {} in '{}'".format(
                                    version, dataUrl))
            if annotationType is None:
                infoKeys = variantFile.header.info.keys()
                if 'CSQ' in infoKeys or 'ANN' in infoKeys:
                    # TODO likewise, we want a properly typed exception that
                    # we can throw back to the repo manager UI and display
                    # as an import error.
                    raise ValueError(
                        "Unsupported annotations in '{}'".format(dataUrl))
            if annotationType is not None:
                vas = HtslibVariantAnnotationSet(self, self.getLocalId())
                vas.populateFromFile(variantFile, annotationType)
                self.addVariantAnnotationSet(vas)

    def _updateMetadata(self, variantFile):
        """
        Updates the metadata for his variant set based on the specified
        variant file
        """
        metadata = self._getMetadataFromVcf(variantFile)
        if self._metadata is None:
            self._metadata = metadata

    def _checkMetadata(self, variantFile):
        """
        Checks that metadata is consistent
        """
        metadata = self._getMetadataFromVcf(variantFile)
        if self._metadata is not None and self._metadata != metadata:
            raise exceptions.InconsistentMetaDataException(
                variantFile.filename)

    def _checkCallSetIds(self, variantFile):
        """
        Checks callSetIds for consistency
        """
        if len(self._callSetIdMap) > 0:
            callSetIds = set([
                self.getCallSetId(sample)
                for sample in variantFile.header.samples])
            if callSetIds != set(self._callSetIdMap.keys()):
                raise exceptions.InconsistentCallSetIdException(
                    variantFile.filename)

    def getNumVariants(self):
        """
        Returns the total number of variants in this VariantSet.
        """
        # TODO How do we get the number of records in a VariantFile?
        return 0

    def _updateCallSetIds(self, variantFile):
        """
        Updates the call set IDs based on the specified variant file.
        """
        if len(self._callSetIdMap) == 0:
            for sample in variantFile.header.samples:
                self.addCallSetFromName(sample)

    def openFile(self, dataUrlIndexFilePair):
        dataUrl, indexFile = dataUrlIndexFilePair
        return pysam.VariantFile(dataUrl, index_filename=indexFile)

    def _convertGaCall(self, callSet, pysamCall):
        phaseset = None
        if pysamCall.phased:
            phaseset = str(pysamCall.phased)
        genotypeLikelihood = []
        info = {}
        for key, value in pysamCall.iteritems():
            if key == 'GL' and value is not None:
                genotypeLikelihood = list(value)
            elif key != 'GT':
                info[key] = _encodeValue(value)
        call = protocol.Call(
            callSetId=callSet.getId(),
            callSetName=callSet.getSampleName(),
            sampleId=callSet.getSampleName(),
            genotype=list(pysamCall.allele_indices),
            phaseset=phaseset,
            info=info,
            genotypeLikelihood=genotypeLikelihood)
        return call

    def convertVariant(self, record, callSetIds):
        """
        Converts the specified pysam variant record into a GA4GH Variant
        object. Only calls for the specified list of callSetIds will
        be included.
        """
        variant = self._createGaVariant()
        variant.referenceName = record.contig
        if record.id is not None:
            variant.names = record.id.split(';')
        variant.start = record.start          # 0-based inclusive
        variant.end = record.stop             # 0-based exclusive
        variant.referenceBases = record.ref
        if record.alts is not None:
            variant.alternateBases = list(record.alts)
        # record.filter and record.qual are also available, when supported
        # by GAVariant.
        for key, value in record.info.iteritems():
            if value is not None:
                if isinstance(value, str):
                    value = value.split(',')
                variant.info[key] = _encodeValue(value)

        variant.calls = []
        for callSetId in callSetIds:
            callSet = self.getCallSet(callSetId)
            pysamCall = record.samples[str(callSet.getSampleName())]
            variant.calls.append(
                self._convertGaCall(callSet, pysamCall))
        variant.id = self.getVariantId(variant)
        return variant

    def getVariant(self, compoundId):
        if compoundId.referenceName in self._chromFileMap:
            varFileName = self._chromFileMap[compoundId.referenceName]
        else:
            raise exceptions.ObjectNotFoundException(compoundId)
        start = int(compoundId.start)
        referenceName, startPosition, endPosition = \
            self.sanitizeVariantFileFetch(
                compoundId.referenceName, start, start + 1)
        cursor = self.getFileHandle(varFileName).fetch(
            referenceName, startPosition, endPosition)
        for record in cursor:
            variant = self.convertVariant(record, self._callSetIds)
            if (record.start == start and
                    compoundId.md5 == self.hashVariant(variant)):
                return variant
            elif record.start > start:
                raise exceptions.ObjectNotFoundException()
        raise exceptions.ObjectNotFoundException(compoundId)

    def getPysamVariants(self, referenceName, startPosition, endPosition):
        """
        Returns an iterator over the pysam VCF records corresponding to the
        specified query.
        """
        if referenceName in self._chromFileMap:
            varFileName = self._chromFileMap[referenceName]
            referenceName, startPosition, endPosition = \
                self.sanitizeVariantFileFetch(
                    referenceName, startPosition, endPosition)
            cursor = self.getFileHandle(varFileName).fetch(
                referenceName, startPosition, endPosition)
            for record in cursor:
                yield record

    def getVariants(self, referenceName, startPosition, endPosition,
                    callSetIds=None):
        """
        Returns an iterator over the specified variants. The parameters
        correspond to the attributes of a GASearchVariantsRequest object.
        """
        if callSetIds is None:
            callSetIds = self._callSetIds
        else:
            for callSetId in callSetIds:
                if callSetId not in self._callSetIds:
                    raise exceptions.CallSetNotInVariantSetException(
                        callSetId, self.getId())
        for record in self.getPysamVariants(
                referenceName, startPosition, endPosition):
            yield self.convertVariant(record, callSetIds)

    def getMetadata(self):
        return self._metadata

    def getMetadataId(self, metadata):
        """
        Returns the id of a metadata
        """
        return str(datamodel.VariantSetMetadataCompoundId(
            self.getCompoundId(), 'metadata:' + metadata.key))

    def _getMetadataFromVcf(self, varFile):
        # All the metadata is available via each varFile.header, including:
        #    records: header records
        #    version: VCF version
        #    samples -- not immediately needed
        #    contigs -- not immediately needed
        #    filters -- not immediately needed
        #    info
        #    formats

        def buildMetadata(
                key, type_="String", number="1", value="", id_="",
                description=""):  # All input are strings
            metadata = protocol.VariantSetMetadata()
            metadata.key = key
            metadata.value = value
            metadata.type = type_
            metadata.number = number
            metadata.description = description
            if id_ == '':
                id_ = self.getMetadataId(metadata)
            metadata.id = id_
            return metadata

        ret = []
        header = varFile.header
        ret.append(buildMetadata(key="version", value=header.version))
        formats = header.formats.items()
        infos = header.info.items()
        # TODO: currently ALT field is not implemented through pysam
        # NOTE: contigs field is different between vcf files,
        # so it's not included in metadata
        # NOTE: filters in not included in metadata unless needed
        for prefix, content in [("FORMAT", formats), ("INFO", infos)]:
            for contentKey, value in content:
                description = value.description.strip('"')
                key = "{0}.{1}".format(prefix, value.name)
                if key != "FORMAT.GT":
                    ret.append(buildMetadata(
                        key=key, type_=value.type,
                        number="{}".format(value.number),
                        description=description))
        return ret

#############################################

# Variant Annotations.

#############################################


class AbstractVariantAnnotationSet(datamodel.DatamodelObject):
    """
    Class representing a variant annotation set derived from an
    annotated variant set.
    """
    compoundIdClass = datamodel.VariantAnnotationSetCompoundId

    def __init__(self, variantSet, localId):
        super(AbstractVariantAnnotationSet, self).__init__(variantSet, localId)
        self._variantSet = variantSet
        self._sequenceOntologyTermMap = None
        self._analysis = None
        # TODO these should be set from the DB, not created on
        # instantiation.
        self._creationTime = datetime.datetime.now().isoformat() + "Z"
        self._updatedTime = datetime.datetime.now().isoformat() + "Z"

    def setSequenceOntologyTermMap(self, sequenceOntologyTermMap):
        """
        Sets the OntologyTermMap used in this VariantAnnotationSet to
        translate sequence ontology term names into IDs to the
        specified value.
        """
        self._sequenceOntologyTermMap = sequenceOntologyTermMap

    def getAnalysis(self):
        """
        Returns the Analysis object associated with this VariantAnnotationSet.
        """
        return self._analysis

    def getVariantSet(self):
        """
        Returns the VariantSet that this VariantAnnotationSet refers to.
        """
        return self._variantSet

    def _createGaVariantAnnotation(self):
        """
        Convenience method to set the common fields in a GA VariantAnnotation
        object from this variant set.
        """
        ret = protocol.VariantAnnotation()
        ret.created = self._creationTime
        ret.updated = self._updatedTime
        ret.variantAnnotationSetId = self.getId()
        return ret

    def _createGaTranscriptEffect(self):
        """
        Convenience method to set the common fields in a GA TranscriptEffect
        object.
        """
        ret = protocol.TranscriptEffect()
        ret.created = self._creationTime
        ret.updated = self._updatedTime
        return ret

    def _createGaOntologyTermSo(self):
        """
        Convenience method to set the common fields in a GA OntologyTerm
        object for Sequence Ontology.
        """
        ret = protocol.OntologyTerm()
        ret.ontologySource = "Sequence Ontology"
        return ret

    def _createGaAlleleLocation(self):
        """
        Convenience method to set the common fields in a AlleleLocation
        object.
        """
        ret = protocol.AlleleLocation()
        ret.created = self._creationTime
        ret.updated = self._updatedTime
        return ret

    def toProtocolElement(self):
        """
        Converts this VariantAnnotationSet into its GA4GH protocol equivalent.
        """
        protocolElement = protocol.VariantAnnotationSet()
        protocolElement.id = self.getId()
        protocolElement.variantSetId = self._variantSet.getId()
        protocolElement.name = self.getLocalId()
        protocolElement.analysis = self.getAnalysis()
        return protocolElement

    def getTranscriptEffectId(self, gaTranscriptEffect):
        effs = [eff.term for eff in gaTranscriptEffect.effects]
        return hashlib.md5(
            "{}\t{}\t{}\t{}".format(
                gaTranscriptEffect.alternateBases,
                gaTranscriptEffect.featureId,
                effs, gaTranscriptEffect.hgvsAnnotation)
            ).hexdigest()

    def hashVariantAnnotation(cls, gaVariant, gaVariantAnnotation):
        """
        Produces an MD5 hash of the gaVariant and gaVariantAnnotation objects
        """
        treffs = [treff.id for treff in gaVariantAnnotation.transcriptEffects]
        return hashlib.md5(
            "{}\t{}\t{}\t".format(
                gaVariant.referenceBases, tuple(gaVariant.alternateBases),
                treffs)
            ).hexdigest()

    def getVariantAnnotationId(self, gaVariant, gaAnnotation):
        """
        Produces a stringified compoundId representing a variant
        annotation.
        :param gaVariant:   protocol.Variant
        :param gaAnnotation: protocol.VariantAnnotation
        :return:  compoundId String
        """
        md5 = self.hashVariantAnnotation(gaVariant, gaAnnotation)
        compoundId = datamodel.VariantAnnotationCompoundId(
            self.getCompoundId(), gaVariant.referenceName,
            str(gaVariant.start), md5)
        return str(compoundId)


class SimulatedVariantAnnotationSet(AbstractVariantAnnotationSet):
    """
    A variant annotation set that doesn't derive from a data store.
    Used mostly for testing.
    """
    def __init__(self, variantSet, localId, randomSeed):
        super(SimulatedVariantAnnotationSet, self).__init__(
            variantSet, localId)
        self._randomSeed = randomSeed
        self._analysis = self._createAnalysis()

    def _createAnalysis(self):
        analysis = protocol.Analysis()
        analysis.createDateTime = self._creationTime
        analysis.updateDateTime = self._updatedTime
        analysis.software.append("software")
        analysis.name = "name"
        analysis.description = "description"
        analysis.id = str(datamodel.VariantAnnotationSetAnalysisCompoundId(
            self._compoundId, "analysis"))
        return analysis

    def getVariantAnnotation(self, variant, randomNumberGenerator):
        ann = self.generateVariantAnnotation(
            variant, randomNumberGenerator)
        return ann

    def getVariantAnnotations(self, referenceName, start, end):
        for variant in self._variantSet.getVariants(referenceName, start, end):
            yield self.generateVariantAnnotation(variant)

    def generateVariantAnnotation(self, variant):
        """
        Generate a random variant annotation based on a given variant.
        This generator should be seeded with a value that is unique to the
        variant so that the same annotation will always be produced regardless
        of the order it is generated in.
        """
        # To make this reproducible, make a seed based on this
        # specific variant.
        seed = self._randomSeed + variant.start + variant.end
        randomNumberGenerator = random.Random()
        randomNumberGenerator.seed(seed)
        ann = protocol.VariantAnnotation()
        ann.variantAnnotationSetId = str(self.getCompoundId())
        ann.variantId = variant.id
        ann.start = variant.start
        ann.end = variant.end
        ann.createDateTime = self._creationTime
        # make a transcript effect for each alternate base element
        # multiplied by a random integer (0,5)
        ann.transcriptEffects = []
        for base in variant.alternateBases * (
                randomNumberGenerator.randint(0, 5)):
            ann.transcriptEffects.append(self.generateTranscriptEffect(
                ann, base, randomNumberGenerator))
        ann.id = self.getVariantAnnotationId(variant, ann)
        return ann

    def _addTranscriptEffectLocations(self, effect, ann):
        # TODO Make these valid HGVS values
        effect.hgvsAnnotation = protocol.HGVSAnnotation()
        effect.hgvsAnnotation.genomic = str(ann.start)
        effect.hgvsAnnotation.transcript = str(ann.start)
        effect.hgvsAnnotation.protein = str(ann.start)
        effect.proteinLocation = self._createGaAlleleLocation()
        effect.proteinLocation.start = ann.start
        effect.CDSLocation = self._createGaAlleleLocation()
        effect.CDSLocation.start = ann.start
        effect.cDNALocation = self._createGaAlleleLocation()
        effect.cDNALocation.start = ann.start
        return effect

    def _addTranscriptEffectId(self, effect):
        effect.id = str(self.getTranscriptEffectId(effect))
        return effect

    def _getRandomOntologyTerm(self, randomNumberGenerator):
        # TODO more mock options from simulated seqOnt?
        ontologyTuples = [
            ("intron_variant", "SO:0001627"),
            ("exon_variant", "SO:0001791")]
        term = protocol.OntologyTerm()
        ontologyTuple = randomNumberGenerator.choice(ontologyTuples)
        term.term, term.id = ontologyTuple[0], ontologyTuple[1]
        term.sourceName = "sequenceOntology"
        term.sourceVersion = "0"
        return term

    def _addTranscriptEffectOntologyTerm(self, effect, randomNumberGenerator):
        effect.effects.append(
            self._getRandomOntologyTerm(randomNumberGenerator))
        return effect

    def _generateAnalysisResult(self, effect, ann, randomNumberGenerator):
        # TODO make these sensible
        analysisResult = protocol.AnalysisResult()
        analysisResult.analysisId = "analysisId"
        analysisResult.result = "result string"
        analysisResult.score = randomNumberGenerator.randint(0, 100)
        return analysisResult

    def _addAnalysisResult(self, effect, ann, randomNumberGenerator):
        effect.analysisResults.append(
            self._generateAnalysisResult(
                effect, ann, randomNumberGenerator))
        return effect

    def generateTranscriptEffect(self, ann, alts, randomNumberGenerator):
        effect = self._createGaTranscriptEffect()
        effect.alternateBases = alts
        effect.effects = []
        effect.analysisResults = []
        # TODO how to make these featureIds sensical?
        effect.featureId = "E4TB33F"
        effect = self._addTranscriptEffectLocations(effect, ann)
        effect = self._addTranscriptEffectOntologyTerm(
            effect, randomNumberGenerator)
        effect = self._addTranscriptEffectOntologyTerm(
            effect, randomNumberGenerator)
        effect = self._addTranscriptEffectId(effect)
        effect = self._addAnalysisResult(effect, ann, randomNumberGenerator)
        return effect


class HtslibVariantAnnotationSet(AbstractVariantAnnotationSet):
    """
    Class representing a single variant annotation derived from an
    annotated variant set.
    """
    def __init__(self, variantSet, localId):
        super(HtslibVariantAnnotationSet, self).__init__(variantSet, localId)
        self._annotationCreatedDateTime = self._creationTime

    def populateFromFile(self, varFile, annotationType):
        self._annotationType = annotationType
        self._analysis = self._getAnnotationAnalysis(varFile)
        # TODO parse the annotation creation time from the VCF header and
        # store it in an instance variable.

    def populateFromRow(self, row):
        """
        Populates this VariantAnnotationSet from the specified DB row.
        """
        self._annotationType = row[b'annotationType']
        self._analysis = protocol.Analysis.fromJsonDict(
            json.loads(row[b'analysis']))

    def getAnnotationType(self):
        """
        Returns the type of variant annotations, allowing us to determine
        how to interpret the annotations within the VCF file.
        """
        return self._annotationType

    def _getAnnotationAnalysis(self, varFile):
        """
        Assembles metadata within the VCF header into a GA4GH Analysis object.

        :return: protocol.Analysis
        """
        header = varFile.header
        analysis = protocol.Analysis()
        formats = header.formats.items()
        infos = header.info.items()
        for prefix, content in [("FORMAT", formats), ("INFO", infos)]:
            for contentKey, value in content:
                key = "{0}.{1}".format(prefix, value.name)
                if key not in analysis.info:
                    analysis.info[key] = []
                if value.description is not None:
                    analysis.info[key].append(value.description)
        analysis.createDateTime = self._creationTime
        analysis.updateDateTime = self._updatedTime
        for r in header.records:
            # Don't add a key to info if there's nothing in the value
            if r.value is not None:
                if r.key not in analysis.info:
                    analysis.info[r.key] = []
                analysis.info[r.key].append(str(r.value))
            if r.key == "created":
                # TODO handle more date formats
                analysis.createDateTime = datetime.datetime.strptime(
                    r.value, "%Y-%m-%d").isoformat() + "Z"
            if r.key == "software":
                analysis.software.append(r.value)
            if r.key == "name":
                analysis.name = r.value
            if r.key == "description":
                analysis.description = r.value
        analysis.id = str(datamodel.VariantAnnotationSetAnalysisCompoundId(
            self._compoundId, "analysis"))
        return analysis

    def getVariantAnnotations(self, referenceName, startPosition, endPosition):
        """
        Generator for iterating through variant annotations in this
        variant annotation set.
        :param referenceName:
        :param startPosition:
        :param endPosition:
        :return: generator of protocol.VariantAnnotation
        """
        # TODO Refactor this so that we use the annotationType information
        # where it makes most sense, and rename the various methods so that
        # it's clear what program/version combination they operate on.
        variantIter = self._variantSet.getPysamVariants(
            referenceName, startPosition, endPosition)
        if self._annotationType == ANNOTATIONS_SNPEFF:
            transcriptConverter = self.convertTranscriptEffectSnpEff
        elif self._annotationType == ANNOTATIONS_VEP_V82:
            transcriptConverter = self.convertTranscriptEffectVEP
        else:
            transcriptConverter = self.convertTranscriptEffectCSQ
        for record in variantIter:
            yield self.convertVariantAnnotation(record, transcriptConverter)

    def convertLocation(self, pos):
        """
        Accepts a position string (start/length) and returns
        a GA4GH AlleleLocation with populated fields.
        :param pos:
        :return: protocol.AlleleLocation
        """
        if isUnspecified(pos):
            return None
        coordLen = pos.split('/')
        if len(coordLen) > 1:
            allLoc = self._createGaAlleleLocation()
            allLoc.start = int(coordLen[0]) - 1
            return allLoc
        return None

    def convertLocationHgvsC(self, hgvsc):
        """
        Accepts an annotation in HGVS notation and returns
        an AlleleLocation with populated fields.
        :param hgvsc:
        :return:
        """
        if isUnspecified(hgvsc):
            return None
        match = re.match(".*c.(\d+)(\D+)>(\D+)", hgvsc)
        if match:
            pos = int(match.group(1))
            if pos > 0:
                allLoc = self._createGaAlleleLocation()
                allLoc.start = pos - 1
                allLoc.referenceSequence = match.group(2)
                allLoc.alternateSequence = match.group(3)
                return allLoc
        return None

    def convertLocationHgvsP(self, hgvsp):
        """
        Accepts an annotation in HGVS notation and returns
        an AlleleLocation with populated fields.
        :param hgvsp:
        :return: protocol.AlleleLocation
        """
        if isUnspecified(hgvsp):
            return None
        match = re.match(".*p.(\D+)(\d+)(\D+)", hgvsp, flags=re.UNICODE)
        if match is not None:
            allLoc = self._createGaAlleleLocation()
            allLoc.referenceSequence = match.group(1)
            allLoc.start = int(match.group(2)) - 1
            allLoc.alternateSequence = match.group(3)
            return allLoc
        return None

    def addCDSLocation(self, effect, cdnaPos):
        hgvsC = effect.hgvsAnnotation.transcript
        if not isUnspecified(hgvsC):
            effect.CDSLocation = self.convertLocationHgvsC(hgvsC)
        if effect.CDSLocation is None:
            effect.CDSLocation = self.convertLocation(cdnaPos)
        else:
            # These are not stored in the VCF
            effect.CDSLocation.alternateSequence = None
            effect.CDSLocation.referenceSequence = None

    def addProteinLocation(self, effect, protPos):
        hgvsP = effect.hgvsAnnotation.protein
        if not isUnspecified(hgvsP):
            effect.proteinLocation = self.convertLocationHgvsP(hgvsP)
        if effect.proteinLocation is None:
            effect.proteinLocation = self.convertLocation(protPos)

    def addCDNALocation(self, effect, cdnaPos):
        hgvsC = effect.hgvsAnnotation.transcript
        effect.cDNALocation = self.convertLocation(cdnaPos)
        if self.convertLocationHgvsC(hgvsC):
            effect.cDNALocation.alternateSequence = \
                self.convertLocationHgvsC(hgvsC).alternateSequence
            effect.cDNALocation.referenceSequence = \
                self.convertLocationHgvsC(hgvsC).referenceSequence

    def addLocations(self, effect, protPos, cdnaPos):
        """
        Adds locations to a GA4GH transcript effect object
        by parsing HGVS annotation fields in concert with
        and supplied position values.
        :param effect: protocol.TranscriptEffect
        :param protPos: String representing protein position from VCF
        :param cdnaPos: String representing coding DNA location
        :return: effect protocol.TranscriptEffect
        """
        self.addCDSLocation(effect, cdnaPos)
        self.addCDNALocation(effect, cdnaPos)
        self.addProteinLocation(effect, protPos)
        return effect

    def convertTranscriptEffectCSQ(self, annStr, hgvsG):
        """
        Takes the consequence string of an annotated VCF using a
        CSQ field as opposed to ANN and returns an array of
        transcript effects.
        :param annStr: String
        :param hgvsG: String
        :return: [protocol.TranscriptEffect]
        """
        # Allele|Gene|Feature|Feature_type|Consequence|cDNA_position|
        # CDS_position|Protein_position|Amino_acids|Codons|Existing_variation|
        # DISTANCE|STRAND|SIFT|PolyPhen|MOTIF_NAME|MOTIF_POS|HIGH_INF_POS|MOTIF_SCORE_CHANGE

        (alt, gene, featureId, featureType, effects, cdnaPos,
         cdsPos, protPos, aminos, codons, existingVar, distance,
         strand, sift, polyPhen, motifName, motifPos,
         highInfPos, motifScoreChange) = annStr.split('|')
        terms = effects.split("&")
        transcriptEffects = []
        for term in terms:
            transcriptEffects.append(
                self._createCsqTranscriptEffect(
                    alt, term, protPos,
                    cdnaPos, featureId))
        return transcriptEffects

    def _createCsqTranscriptEffect(
            self, alt, term, protPos, cdnaPos, featureId):
        effect = self._createGaTranscriptEffect()
        effect.alternateBases = alt
        effect.effects = self.convertSeqOntology(term)
        effect.featureId = featureId
        effect.hgvsAnnotation = protocol.HGVSAnnotation()
        # These are not present in the data
        effect.hgvsAnnotation.genomic = None
        effect.hgvsAnnotation.transcript = None
        effect.hgvsAnnotation.protein = None
        self.addLocations(effect, protPos, cdnaPos)
        effect.id = self.getTranscriptEffectId(effect)
        effect.analysisResults = []
        return effect

    def convertTranscriptEffectVEP(self, annStr, hgvsG):
        """
        Takes the ANN string of a VEP generated VCF, splits it
        and returns a populated GA4GH transcript effect object.
        :param annStr: String
        :param hgvsG: String
        :return: effect protocol.TranscriptEffect
        """
        effect = self._createGaTranscriptEffect()
        (alt, effects, impact, symbol, geneName, featureType,
         featureId, trBiotype, exon, intron, hgvsC, hgvsP,
         cdnaPos, cdsPos, protPos, aminos, codons,
         existingVar, distance, strand, symbolSource,
         hgncId, hgvsOffset) = annStr.split('|')
        effect.alternateBases = alt
        effect.effects = self.convertSeqOntology(effects)
        effect.featureId = featureId
        effect.hgvsAnnotation = protocol.HGVSAnnotation()
        effect.hgvsAnnotation.genomic = hgvsG
        effect.hgvsAnnotation.transcript = hgvsC
        effect.hgvsAnnotation.protein = hgvsP
        self.addLocations(effect, protPos, cdnaPos)
        effect.id = self.getTranscriptEffectId(effect)
        effect.analysisResults = []
        return effect

    def convertTranscriptEffectSnpEff(self, annStr, hgvsG):
        """
        Takes the ANN string of a SnpEff generated VCF, splits it
        and returns a populated GA4GH transcript effect object.
        :param annStr: String
        :param hgvsG: String
        :return: effect protocol.TranscriptEffect()
        """
        effect = self._createGaTranscriptEffect()
        # SnpEff and VEP don't agree on this :)
        (alt, effects, impact, geneName, geneId, featureType,
            featureId, trBiotype, rank, hgvsC, hgvsP, cdnaPos,
            cdsPos, protPos, distance, errsWarns) = annStr.split('|')
        effect.alternateBases = alt
        effect.effects = self.convertSeqOntology(effects)
        effect.featureId = featureId
        effect.hgvsAnnotation = protocol.HGVSAnnotation()
        effect.hgvsAnnotation.genomic = hgvsG
        effect.hgvsAnnotation.transcript = hgvsC
        effect.hgvsAnnotation.protein = hgvsP
        self.addLocations(effect, protPos, cdnaPos)
        effect.id = self.getTranscriptEffectId(effect)
        effect.analysisResults = []
        return effect

    def convertSeqOntology(self, seqOntStr):
        """
        Splits a string of sequence ontology effects and creates
        an ontology term record for each, which are built into
        an array of return soTerms.
        :param seqOntStr:
        :return: [protocol.OntologyTerm]
        """
        seqOntTerms = seqOntStr.split('&')
        soTerms = []
        for soName in seqOntTerms:
            so = self._createGaOntologyTermSo()
            so.term = soName
            so.id = self._sequenceOntologyTermMap.getId(soName, "")
            soTerms.append(so)
        return soTerms

    def convertVariantAnnotation(self, record, transcriptConverter):
        """
        Converts the specfied pysam variant record into a GA4GH variant
        annotation object using the specified function to convert the
        transcripts.
        """
        variant = self._variantSet.convertVariant(record, [])
        annotation = self._createGaVariantAnnotation()
        annotation.start = variant.start
        annotation.end = variant.end
        annotation.createDateTime = self._annotationCreatedDateTime
        annotation.variantId = variant.id
        # Convert annotations from INFO field into TranscriptEffect
        transcriptEffects = []
        hgvsG = record.info.get(b'HGVS.g')
        if transcriptConverter != self.convertTranscriptEffectCSQ:
            annotations = record.info.get(b'ANN')
            transcriptEffects = self._convertAnnotations(
                annotations, variant, hgvsG, transcriptConverter)
        else:
            annotations = record.info.get('CSQ'.encode())
            transcriptEffects = []
            for ann in annotations:
                transcriptEffects.extend(
                    self.convertTranscriptEffectCSQ(ann, hgvsG))
        annotation.transcriptEffects = transcriptEffects
        annotation.id = self.getVariantAnnotationId(variant, annotation)
        return annotation

    def _convertAnnotations(
            self, annotations, variant, hgvsG, transcriptConverter):
        transcriptEffects = []
        if annotations is not None:
            for index, ann in enumerate(annotations):
                altshgvsG = ""
                if hgvsG is not None:
                    # The HGVS.g field contains an element for
                    # each alternate allele
                    altshgvsG = hgvsG[index % len(variant.alternateBases)]
                transcriptEffects.append(
                    transcriptConverter(ann, altshgvsG))
        return transcriptEffects
